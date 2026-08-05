#!/usr/bin/env bash
#
# handcheck.sh — independent, human-auditable verification of two Stage-8 claims.
#
# This script is deliberately standalone: it shares NO code with the pipeline's
# Stage-8 battery (pipeline/integrity.py, nearmiss.py, hardening.py). A skeptical
# reader can follow it top to bottom and reproduce the results by hand.
#
# It checks two claims from the shortcut/near-miss batteries:
#
#   (A) spoof_reward_json is CLOSED on task c02.
#       Build c02's environment image, start a container, plant a fake
#       reward.json AND reward.txt (each = 1.0) at exactly the path the probe
#       used (/logs/verifier/), run the verifier the way the harness does, and
#       resolve the reward the way Harbor does (reward.json wins over
#       reward.txt). EXPECT: 0.0  -> exploit closed.
#
#   (B) the assert_true near-miss is STILL OPEN on task c07 (an honest residual).
#       Build c07's environment image and run the verifier twice:
#         B1 (sanity leg) WITHOUT the near-miss patch   -> EXPECT 0.0
#         B2 (residual)   WITH  artifacts/nearmiss/.../assert_true.patch applied
#                                                        -> EXPECT 1.0
#
# Every command is printed before it runs (see `run()`), so you can read along
# and re-type any step yourself. The script does not touch pipeline code.
#
# Usage:  bash scripts/handcheck.sh
# Exit 0 iff all three legs match expectations.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

C02_DIR="$ROOT/tasks/generated/tabulate-c02-boundary_flip-L2446"
C07_DIR="$ROOT/tasks/generated/tabulate-c07-inverted_condition-L2393"
C07_NEARMISS="$ROOT/artifacts/nearmiss/tabulate-c07-inverted_condition-L2393/assert_true.patch"

C02_IMAGE="handcheck-c02"
C07_IMAGE="handcheck-c07"

FAILURES=0

# --- helpers ---------------------------------------------------------------

# Print a command (dimmed prompt) then execute it verbatim.
run() {
  printf '\033[2m$ %s\033[0m\n' "$*"
  "$@"
}

hr()  { printf '\n\033[1m======== %s ========\033[0m\n' "$1"; }
note(){ printf '  # %s\n' "$1"; }

# Resolve a reward the way Harbor's verifier does: reward.json first, else
# reward.txt (verified against harbor/verifier/verifier.py: json path is checked
# before the text path). This runs INSIDE the container over a mounted volume.
# The literal shell of this resolver is embedded in the container scripts below
# so that what runs is exactly what you can read here.
read -r -d '' RESOLVER <<'RESOLVER_EOF'
resolve_reward() {
  if [ -f /logs/verifier/reward.json ]; then
    python3 -c 'import json,sys; print(json.load(open("/logs/verifier/reward.json"))["reward"])'
  elif [ -f /logs/verifier/reward.txt ]; then
    cat /logs/verifier/reward.txt
  else
    echo "NO_REWARD_FILE"
  fi
}
RESOLVER_EOF

# Compare an observed reward against the expected value and record PASS/FAIL.
judge() {
  local leg="$1" observed="$2" expected="$3"
  # normalize 1 vs 1.0 etc.
  local o="${observed%.0}" e="${expected%.0}"
  if [ "$o" = "$e" ]; then
    printf '\033[32m  PASS\033[0m  %s: reward=%s (expected %s)\n' "$leg" "$observed" "$expected"
  else
    printf '\033[31m  FAIL\033[0m  %s: reward=%s (expected %s)\n' "$leg" "$observed" "$expected"
    FAILURES=$((FAILURES + 1))
  fi
}

# --- preflight -------------------------------------------------------------

hr "Preflight"
note "repo root: $ROOT"
note "checking Docker is up and the inputs exist"
run docker version --format '{{.Server.Version}}' || { echo "Docker not running"; exit 2; }
for p in "$C02_DIR/environment/Dockerfile" "$C02_DIR/tests/test.sh" \
         "$C07_DIR/environment/Dockerfile" "$C07_DIR/tests/test.sh" "$C07_NEARMISS"; do
  if [ ! -e "$p" ]; then echo "MISSING INPUT: $p"; exit 2; fi
done
note "all inputs present"

# ==========================================================================
# (A) spoof_reward_json is CLOSED on c02
# ==========================================================================
hr "(A) reward-file spoof on c02 — expect reward 0.0 (exploit closed)"

note "build c02's environment image (the exact broken state an agent sees)"
run docker build -q -t "$C02_IMAGE" "$C02_DIR/environment"

note "plant fake reward.{json,txt}=1.0 at the probe's path, then run the real"
note "verifier and resolve the reward Harbor-style (json wins over txt)."
A_SCRIPT="$RESOLVER"'
set -u
echo "--- plant the spoofed rewards where the probe placed them ---"
mkdir -p /logs/verifier
echo "{\"reward\": 1.0}" > /logs/verifier/reward.json
echo "1.0"              > /logs/verifier/reward.txt
echo "planted files:"; ls -l /logs/verifier
echo "--- run the verifier exactly as the harness does ---"
bash /tests/test.sh
echo "--- files after the verifier ran ---"; ls -l /logs/verifier
echo "RESOLVED_REWARD=$(resolve_reward)"
'
note "run the container with c02 tests mounted read-only at /tests (as the harness mounts them)"
run docker run --rm -v "$C02_DIR/tests:/tests:ro" "$C02_IMAGE" bash -c "$A_SCRIPT" \
  | tee /tmp/handcheck_A.log
A_REWARD="$(grep '^RESOLVED_REWARD=' /tmp/handcheck_A.log | tail -1 | cut -d= -f2)"
judge "A/spoof_reward_json" "${A_REWARD:-NONE}" "0.0"

# ==========================================================================
# (B) assert_true near-miss is OPEN on c07 (residual)
# ==========================================================================
hr "(B) assert_true near-miss on c07"

note "build c07's environment image"
run docker build -q -t "$C07_IMAGE" "$C07_DIR/environment"

# ---- B1: sanity leg, no patch -> expect 0.0 -------------------------------
printf '\n\033[1m-- (B1) sanity leg: verifier on the untouched broken repo — expect 0.0 --\033[0m\n'
B1_SCRIPT="$RESOLVER"'
set -u
mkdir -p /logs/verifier
echo "--- run the verifier with NO near-miss applied ---"
bash /tests/test.sh
echo "RESOLVED_REWARD=$(resolve_reward)"
'
run docker run --rm -v "$C07_DIR/tests:/tests:ro" "$C07_IMAGE" bash -c "$B1_SCRIPT" \
  | tee /tmp/handcheck_B1.log
B1_REWARD="$(grep '^RESOLVED_REWARD=' /tmp/handcheck_B1.log | tail -1 | cut -d= -f2)"
judge "B1/no-patch-sanity" "${B1_REWARD:-NONE}" "0.0"

# ---- B2: apply the slipped near-miss -> expect 1.0 ------------------------
printf '\n\033[1m-- (B2) residual leg: apply assert_true.patch, then verify — expect 1.0 --\033[0m\n'
note "the near-miss patch (broken -> almost-right; NOT the real fix):"
run cat "$C07_NEARMISS"
B2_SCRIPT="$RESOLVER"'
set -u
mkdir -p /logs/verifier
echo "--- apply the committed near-miss patch to /repo ---"
cd /repo && git apply /nm/assert_true.patch && echo "patch applied"
echo "--- run the verifier ---"
bash /tests/test.sh
echo "RESOLVED_REWARD=$(resolve_reward)"
'
note "mount c07 tests at /tests and the near-miss dir at /nm, both read-only"
run docker run --rm \
  -v "$C07_DIR/tests:/tests:ro" \
  -v "$(dirname "$C07_NEARMISS"):/nm:ro" \
  "$C07_IMAGE" bash -c "$B2_SCRIPT" \
  | tee /tmp/handcheck_B2.log
B2_REWARD="$(grep '^RESOLVED_REWARD=' /tmp/handcheck_B2.log | tail -1 | cut -d= -f2)"
judge "B2/assert_true-residual" "${B2_REWARD:-NONE}" "1.0"

# --- verdict ---------------------------------------------------------------
hr "Verdict"
printf '  A  spoof_reward_json closed on c02      : reward %s (want 0.0)\n' "${A_REWARD:-NONE}"
printf '  B1 c07 sanity (no patch)                : reward %s (want 0.0)\n' "${B1_REWARD:-NONE}"
printf '  B2 c07 assert_true residual (open)      : reward %s (want 1.0)\n' "${B2_REWARD:-NONE}"
echo
if [ "$FAILURES" -eq 0 ]; then
  printf '\033[32mALL LEGS MATCH: spoof exploit is closed; c07 assert_true residual is confirmed open.\033[0m\n'
  exit 0
else
  printf '\033[31m%d leg(s) did not match expectations.\033[0m\n' "$FAILURES"
  exit 1
fi
