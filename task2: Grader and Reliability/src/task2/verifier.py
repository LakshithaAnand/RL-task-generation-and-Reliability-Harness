"""Runs the official TB2 verifier (tests/test.sh) inside a container.

Isolation rule: the tests executed at verification time are ALWAYS a clean copy
injected from the pristine host-side task directory. Anything the agent left at
/tests is deleted first, so editing or deleting tests during the attempt can
never change what the verifier runs (it can only trip the integrity tripwire).

The verifier writes two artifacts inside the container:
  /logs/verifier/ctrf.json    per-test results (pytest-json-ctrf)
  /logs/verifier/reward.txt   official binary signal: 1 = all tests passed
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from task2.tasks import Task


@dataclass
class VerifierResult:
    runnable: bool             # test.sh completed and produced parseable CTRF
    all_passed: bool           # official binary signal (reward.txt == "1")
    passed: int
    failed: int
    total: int
    per_test: list[dict] = field(default_factory=list)  # [{"name", "status"}]
    exit_code: int = 0
    stdout_tail: str = ""
    error: str | None = None

    def summary(self) -> str:
        if not self.runnable:
            return f"verifier NOT RUNNABLE ({self.error})"
        return f"{self.passed}/{self.total} passed, all_passed={self.all_passed}"


def run_verifier(container, task: Task) -> VerifierResult:
    """Inject clean tests and run test.sh. `container` is environment.Container."""
    # Clean-room injection: wipe whatever is at /tests, copy pristine tests in.
    container.exec_root("rm -rf /tests /logs/verifier && mkdir -p /logs/verifier")
    container.cp_in(task.tests_dir, "/tests")

    res = container.exec_root(
        "bash /tests/test.sh",
        cwd=container.workdir,
        timeout=task.verifier_timeout_sec,
    )
    if res.timed_out:
        return VerifierResult(
            runnable=False, all_passed=False, passed=0, failed=0, total=0,
            exit_code=res.exit_code, stdout_tail=res.stdout[-2000:],
            error=f"verifier timed out after {task.verifier_timeout_sec}s",
        )

    ctrf_raw = container.read_file("/logs/verifier/ctrf.json")
    reward_raw = container.read_file("/logs/verifier/reward.txt")
    if ctrf_raw is None:
        return VerifierResult(
            runnable=False, all_passed=False, passed=0, failed=0, total=0,
            exit_code=res.exit_code, stdout_tail=res.stdout[-2000:],
            error="verifier produced no ctrf.json (crash or workspace corrupted)",
        )

    try:
        ctrf = json.loads(ctrf_raw)
        summary = ctrf["results"]["summary"]
        per_test = [
            {"name": t.get("name", "?"), "status": t.get("status", "?")}
            for t in ctrf["results"].get("tests", [])
        ]
    except (json.JSONDecodeError, KeyError) as e:
        return VerifierResult(
            runnable=False, all_passed=False, passed=0, failed=0, total=0,
            exit_code=res.exit_code, stdout_tail=res.stdout[-2000:],
            error=f"ctrf.json unparseable: {e}",
        )

    all_passed = (reward_raw or "").strip() == "1"
    return VerifierResult(
        runnable=True,
        all_passed=all_passed,
        passed=int(summary.get("passed", 0)),
        failed=int(summary.get("failed", 0)),
        total=int(summary.get("tests", 0)),
        per_test=per_test,
        exit_code=res.exit_code,
        stdout_tail=res.stdout[-2000:],
    )
