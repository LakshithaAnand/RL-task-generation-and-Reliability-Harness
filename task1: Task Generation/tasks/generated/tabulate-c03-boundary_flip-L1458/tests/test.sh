#!/bin/bash
# Held-out verifier (Stage 5 synthesis + Stage 8 hardening).
# Runs flipped regression tests (regression_copy) + admitted edge tests.
# Reward 1 iff ALL pass.
set -u
mkdir -p /logs/verifier
# [harden: de-spoof] only THIS script's verdict may set the reward
rm -f /logs/verifier/reward.json /logs/verifier/reward.txt
# [harden: de-poison] drop a hostile conftest an agent may have planted
rm -f /repo/conftest.py
# [harden: pin code-under-test] resolve `import tabulate` to /repo, never a
# healthy copy pip-installed into site-packages
export PYTHONPATH=/repo:${PYTHONPATH:-}
cd /repo
EDGE_ARGS=""
[ -d /tests/edge ] && EDGE_ARGS="/tests/edge"
tr '\n' '\0' < /tests/flipped_tests.txt \
  | xargs -0 timeout 240 python -m pytest -q --tb=short \
      -p no:cacheprovider $EDGE_ARGS
code=$?
if [ $code -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
echo "verifier pytest exit: $code"
