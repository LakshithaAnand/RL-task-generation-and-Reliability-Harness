#!/usr/bin/env bash
# check_env.sh — Stage 0 environment verification.
#
# Re-verifies the full toolchain in one command and exits nonzero on any failure:
#   1. Python >= 3.11
#   2. uv present
#   3. Docker present + daemon running
#   4. Harbor CLI present
#   5. The _smoke/hello Harbor task runs end-to-end:
#        oracle agent -> reward 1.0   (task is solvable)
#        nop    agent -> reward 0.0   (task starts unsolved)
#
# No API keys required. All checks are deterministic.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"   # harbor/uv install location

FAILURES=0
pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
hdr()  { printf '\n== %s ==\n' "$1"; }

# --- 1. Python >= 3.11 ---------------------------------------------------------
hdr "Python"
if command -v python3 >/dev/null 2>&1; then
  PYVER="$(python3 -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])')"
  if python3 -c 'import sys;sys.exit(0 if sys.version_info[:2]>=(3,11) else 1)'; then
    pass "python3 $PYVER (>= 3.11)"
  else
    fail "python3 $PYVER is < 3.11"
  fi
else
  fail "python3 not found"
fi

# --- 2. uv ---------------------------------------------------------------------
hdr "uv"
if command -v uv >/dev/null 2>&1; then
  pass "uv $(uv --version 2>&1 | awk '{print $2}')"
else
  fail "uv not found"
fi

# --- 3. Docker + daemon --------------------------------------------------------
hdr "Docker"
if command -v docker >/dev/null 2>&1; then
  pass "docker client $(docker --version 2>&1 | awk '{print $3}' | tr -d ',')"
  if docker info >/dev/null 2>&1; then
    pass "docker daemon running (server $(docker info --format '{{.ServerVersion}}' 2>/dev/null))"
  else
    fail "docker daemon not running (start Docker Desktop)"
  fi
else
  fail "docker not found"
fi

# --- 4. Harbor CLI -------------------------------------------------------------
hdr "Harbor CLI"
if command -v harbor >/dev/null 2>&1; then
  pass "harbor $(harbor --version 2>&1 | head -1)"
else
  fail "harbor not found (install: uv tool install harbor)"
fi

# --- 5. Smoke task end-to-end --------------------------------------------------
# Only attempt if the prerequisites above held; otherwise the run cannot succeed.
hdr "Harbor smoke task (oracle=1.0, nop=0.0)"
SMOKE_DIR="$ROOT/tasks/_smoke"          # dataset dir; contains the hello/ task
JOBS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/harbor-check.XXXXXX")"
trap 'rm -rf "$JOBS_DIR"' EXIT

reward_of() {  # $1 = result.json ; prints the single eval's mean reward
  python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
evals = d["stats"]["evals"]
mean = next(iter(evals.values()))["metrics"][0]["mean"]
print(f"{mean}")
' "$1"
}

run_agent() {  # $1 = agent name ; prints reward or nothing on failure
  local agent="$1" name="$2"
  if harbor run -p "$SMOKE_DIR" -a "$agent" -o "$JOBS_DIR" \
       --job-name "$name" -q -y >/dev/null 2>&1; then
    reward_of "$JOBS_DIR/$name/result.json" 2>/dev/null
  fi
}

if [ "$FAILURES" -eq 0 ]; then
  ORACLE_REWARD="$(run_agent oracle check-oracle)"
  if [ "$ORACLE_REWARD" = "1.0" ]; then
    pass "oracle reward = 1.0"
  else
    fail "oracle reward = '${ORACLE_REWARD:-<none>}' (expected 1.0)"
  fi

  NOP_REWARD="$(run_agent nop check-nop)"
  if [ "$NOP_REWARD" = "0.0" ]; then
    pass "nop reward = 0.0"
  else
    fail "nop reward = '${NOP_REWARD:-<none>}' (expected 0.0)"
  fi
else
  fail "skipped smoke run: prerequisite checks above failed"
fi

# --- Summary -------------------------------------------------------------------
hdr "Summary"
if [ "$FAILURES" -eq 0 ]; then
  printf '  \033[32mAll checks passed.\033[0m\n'
  exit 0
else
  printf '  \033[31m%d check(s) failed.\033[0m\n' "$FAILURES"
  exit 1
fi
