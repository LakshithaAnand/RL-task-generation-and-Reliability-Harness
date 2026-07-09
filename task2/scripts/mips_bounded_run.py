"""Bounded make-mips-interpreter run (spec + user rules).

Whole thing capped at 1 hour, including the Docker build:
  1. oracle attempt first (includes the image build)
  2. if oracle succeeds and time remains: 1 low-budget live attempt
  3. if time still remains: 1 medium-budget live attempt
On stall past the cap: stop, fall back to oracle + whatever completed.
On outright failure: recorded as a limitation.

Everything is logged (start/end times, command, status, rc) to
data/reports/mips_run_log.txt. Attempts are saved with dataset=analysis_hard:
pooled into the headline agreement N alongside the light-task dataset, and
also broken out as their own subset in the report.

Run from the task2 repo root: .venv/bin/python scripts/mips_bounded_run.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CAP_SEC = 3600
MIN_REMAINING_FOR_LIVE = 300  # don't start a live attempt with <5 min left

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "reports" / "mips_run_log.txt"
TASK2 = ROOT / ".venv" / "bin" / "task2"

START = time.monotonic()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def note(msg: str) -> None:
    line = f"[{now()}] {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def remaining() -> int:
    return max(0, int(CAP_SEC - (time.monotonic() - START)))


def run_stage(label: str, args: list[str]) -> bool:
    budget = remaining()
    if budget <= 0:
        note(f"STAGE {label}: SKIPPED (cap exhausted)")
        return False
    cmd = [str(TASK2), *args]
    note(f"STAGE {label}: START (budget {budget}s)")
    note(f"CMD: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              errors="replace", timeout=budget)
        tail = "\n".join(proc.stdout.splitlines()[-12:])
        with LOG.open("a") as f:
            f.write(tail + "\n")
        status = "OK" if proc.returncode == 0 else f"FAILED rc={proc.returncode}"
        note(f"STAGE {label}: {status}")
        if proc.returncode != 0:
            with LOG.open("a") as f:
                f.write(proc.stderr[-2000:] + "\n")
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        note(f"STAGE {label}: STALLED past cap — stopped (falling back per rules)")
        subprocess.run(["bash", "-c",
                        "docker ps -q --filter name=task2-make-mips-interpreter | xargs -r docker rm -f"],
                       capture_output=True)
        return False


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    note(f"START bounded make-mips-interpreter run (cap {CAP_SEC}s, dataset=analysis_hard)")

    oracle_ok = run_stage("oracle", [
        "attempt", "make-mips-interpreter", "--agent", "oracle",
        "--verify", "--save", "--dataset", "analysis_hard",
    ])

    live_done = 0
    if oracle_ok and remaining() > MIN_REMAINING_FOR_LIVE:
        if run_stage("live-low-budget", [
            "attempt", "make-mips-interpreter", "--agent", "claude",
            "--max-steps", "6", "--temperature", "0.0", "--seed", "9001",
            "--verify", "--save", "--dataset", "analysis_hard",
        ]):
            live_done += 1
    if oracle_ok and remaining() > MIN_REMAINING_FOR_LIVE:
        if run_stage("live-medium-budget", [
            "attempt", "make-mips-interpreter", "--agent", "claude",
            "--max-steps", "12", "--temperature", "0.3", "--seed", "9002",
            "--verify", "--save", "--dataset", "analysis_hard",
        ]):
            live_done += 1

    elapsed = int(time.monotonic() - START)
    if not oracle_ok:
        note(f"END status=ORACLE_FAILED_OR_STALLED live_attempts={live_done} elapsed={elapsed}s "
             "-> record as limitation; ablation falls back to longest/noisiest light-task trajectory")
        sys.exit(1)
    note(f"END status=OK oracle=pass live_attempts={live_done} elapsed={elapsed}s")


if __name__ == "__main__":
    main()
