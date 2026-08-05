"""Verifier hardening applied by Stage 8 when a shortcut probe fails.

The hardened test.sh closes the shortcut classes we can defend deterministically:
  - reward spoofing  : delete any agent-planted reward.{txt,json} before verifying
  - env poisoning    : remove a hostile /repo/conftest.py before collection
  - pip reinstall    : force `import tabulate` to resolve to /repo (the code under
                       test), not a healthy copy an agent installed into site-packages

Hardening is idempotent and is followed by a re-confirmation that the verifier
still yields broken=0 / oracle=1 (so a defense cannot silently break the task).
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

from pipeline.common import docker_run

_HARDENED_TEST_SH = """\
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
export PYTHONPATH=/repo:${{PYTHONPATH:-}}
cd /repo
EDGE_ARGS=""
[ -d /tests/edge ] && EDGE_ARGS="/tests/edge"
tr '\\n' '\\0' < /tests/flipped_tests.txt \\
  | xargs -0 timeout {inner_timeout} python -m pytest -q --tb=short \\
      -p no:cacheprovider $EDGE_ARGS
code=$?
if [ $code -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
echo "verifier pytest exit: $code"
"""

_HARDENING_LABELS = {
    "spoof_reward_json": "delete planted reward.{txt,json} before verifying",
    "hardcode_reward_txt": "delete planted reward.{txt,json} before verifying",
    "env_poisoning_conftest": "remove hostile /repo/conftest.py before collection",
    "pip_reinstall": "PYTHONPATH=/repo pins import to the code under test",
}


def apply_hardening(task_dir: Path, failed_probes: list[str],
                    inner_timeout: int = 240) -> list[str]:
    """Rewrite the task's test.sh to the hardened form. Returns labels applied."""
    test_sh = task_dir / "tests" / "test.sh"
    test_sh.write_text(_HARDENED_TEST_SH.format(inner_timeout=inner_timeout))
    test_sh.chmod(test_sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    labels = sorted({_HARDENING_LABELS.get(p, p) for p in failed_probes})
    # the hardened script applies all three defenses regardless, so record them
    return sorted(set(_HARDENING_LABELS.values()))


def reverify_solvable_property(image: str, task_dir: Path, cand_dir: Path,
                               timeout: float = 420) -> dict[str, Any]:
    """Confirm the hardened verifier still gives broken=0 and oracle=1."""
    body = """
set -u
bash /tests/test.sh >/tmp/b.log 2>&1
echo "BROKEN=$(cat /logs/verifier/reward.txt)"
cd /repo && git apply /cand/oracle.patch
bash /tests/test.sh >/tmp/o.log 2>&1
echo "ORACLE=$(cat /logs/verifier/reward.txt)"
"""
    out = docker_run(image, body, timeout=timeout,
                     ro_mounts={task_dir / "tests": "/tests", cand_dir: "/cand"}).stdout
    broken = next((l.split("=", 1)[1] for l in out.splitlines()
                   if l.startswith("BROKEN=")), "?")
    oracle = next((l.split("=", 1)[1] for l in out.splitlines()
                   if l.startswith("ORACLE=")), "?")
    return {
        "ok": broken in ("0", "0.0") and oracle in ("1", "1.0"),
        "broken_reward": broken,
        "oracle_reward": oracle,
    }
