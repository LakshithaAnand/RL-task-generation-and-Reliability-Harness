#!/usr/bin/env bash
#
# demo.sh — ONE command that re-runs the full pipeline from committed state.
#
#   eligibility -> mutations -> failure establishment -> Harbor assembly ->
#   verifier synthesis -> alignment -> solvability (real `harbor run`) ->
#   shortcut battery (+ registry startup assert) -> near-miss battery
#   (committed patches re-applied and re-scored) -> registry check ->
#   difficulty/diversity tagging -> assurance cards -> funnel report
#
# ZERO API keys: common model keys are actively scrubbed below, so if any
# stage tried to call a model it would fail loudly. Model-generated inputs
# (near-miss patches) are committed and only re-applied/re-scored here.
#
# Requirements: Docker running, `uv`, and the `harbor` CLI (solvability gate).
#
# Usage: make demo   (or: bash scripts/demo.sh)

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- zero API keys, enforced -------------------------------------------------
unset ANTHROPIC_API_KEY OPENAI_API_KEY OPENROUTER_API_KEY GEMINI_API_KEY \
      GOOGLE_API_KEY MISTRAL_API_KEY 2>/dev/null || true

PY="uv run python"

# --- preflight ---------------------------------------------------------------
echo "== preflight =="
command -v uv >/dev/null || { echo "FATAL: uv not found"; exit 2; }
docker version --format 'docker server {{.Server.Version}}' 2>/dev/null \
  || { echo "FATAL: Docker is not running"; exit 2; }
export PATH="$HOME/.local/bin:$PATH"
command -v harbor >/dev/null \
  || { echo "FATAL: harbor CLI not found (uv tool install harbor)"; exit 2; }
echo "preflight ok (model API keys scrubbed from the environment)"

# --- staged run with timing --------------------------------------------------
STAGE_NAMES=()
STAGE_SECS=()
TOTAL_START=$(date +%s)

run_stage() {
  local name="$1"; shift
  printf '\n\033[1m== %s ==\033[0m\n' "$name"
  local start rc
  start=$(date +%s)
  "$@"
  rc=$?
  local secs=$(( $(date +%s) - start ))
  STAGE_NAMES+=("$name"); STAGE_SECS+=("$secs")
  if [ $rc -ne 0 ]; then
    printf '\033[31mSTAGE FAILED (%s, exit %d after %ds) — stopping.\033[0m\n' \
      "$name" "$rc" "$secs"
    exit $rc
  fi
  printf '\033[2m(%s: %ds)\033[0m\n' "$name" "$secs"
}

run_stage "1  eligibility"            $PY -m pipeline.eligibility
run_stage "2  mutations"              $PY -m pipeline.mutations
run_stage "3  failure establishment"  $PY -m pipeline.failure_check
run_stage "4  harbor assembly"        $PY -m pipeline.assemble
run_stage "5  verifier synthesis"     $PY -m pipeline.verifier_synth
run_stage "6  alignment"              $PY -m pipeline.alignment
run_stage "7  solvability (harbor)"   $PY -m pipeline.solvability
run_stage "8a shortcut battery"       $PY -m pipeline.integrity
run_stage "8b near-miss battery"      $PY -m pipeline.nearmiss
run_stage "8c registry check"         $PY -m pipeline.registry
run_stage "9  difficulty+diversity"   $PY -m pipeline.metadata_tag
run_stage "10a assurance cards"       $PY -m pipeline.card_writer
run_stage "10b funnel report"         $PY -m pipeline.funnel_report

# --- summary -------------------------------------------------------------------
TOTAL=$(( $(date +%s) - TOTAL_START ))
printf '\n\033[1m== demo summary ==\033[0m\n'
for i in "${!STAGE_NAMES[@]}"; do
  printf '  %-26s %4ds\n' "${STAGE_NAMES[$i]}" "${STAGE_SECS[$i]}"
done
printf '  %-26s %4ds  (%dm%02ds)\n' "TOTAL" "$TOTAL" $((TOTAL / 60)) $((TOTAL % 60))
N_TASKS=$($PY -c "import json;print(json.load(open('artifacts/assembly_report.json'))['n_assembled'])")
echo
echo "  tasks accepted        : $N_TASKS (tasks/generated/)"
echo "  assurance cards       : artifacts/cards/ (+ one per task folder)"
echo "  funnel report         : artifacts/funnel_report.txt"
echo "  independent handcheck : bash scripts/handcheck.sh (optional, standalone)"
echo
echo "done — full pipeline re-ran from committed state with zero API keys."
