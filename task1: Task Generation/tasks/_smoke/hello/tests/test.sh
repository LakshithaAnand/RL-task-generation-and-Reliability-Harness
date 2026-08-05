#!/bin/bash
# Harbor verifier for the _smoke/hello task.
# Copied to /tests/test.sh and run from the working directory by the harness.
# Reward contract: write a single number to /logs/verifier/reward.txt.
#   1 => task solved (file present with exact expected contents)
#   0 => task not solved
# Deterministic: no model, no network, exact string match only.

set -u

mkdir -p /logs/verifier

target="/app/hello.txt"
expected="hello world"

if [ -f "$target" ] && [ "$(cat "$target")" = "$expected" ]; then
  echo "PASS: $target contains expected contents"
  echo 1 > /logs/verifier/reward.txt
else
  echo "FAIL: $target missing or contents != '$expected'"
  echo 0 > /logs/verifier/reward.txt
fi
