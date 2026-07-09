"""Empirical difficulty — a SEPARATE, model-dependent evaluation step.

NOT part of demo.sh: this needs an Anthropic API key. The zero-key demo only
re-reads the committed artifacts/empirical_difficulty.json this step writes.

For each generated task, run a cheap baseline agent through the real Harbor
harness for N attempts (default 5) and anchor a difficulty label on the
measured solve-rate using thresholds PRE-COMMITTED in docs/PLAN.md — fixed
before any empirical results were seen:

  easy   solve-rate >= 75%
  medium 25% <= solve-rate < 75%
  hard   solve-rate < 25%

Agent/model defaults were confirmed against `harbor run --help` (agent list
includes claude-code; -m passes the model name through): claude-code with
claude-haiku-4-5-20251001, the cheapest current Haiku-class model. Attempts
are parallelized by Harbor's local Docker provider (-k attempts per task,
-n concurrent trials).

API key: read from the environment first, else from a gitignored .env at the
repo root. The runner REFUSES to read .env unless `git check-ignore` says it
is ignored, and never prints or logs the key. Keyless agents (nop, oracle)
skip the requirement — useful for wiring smoke tests.

Small-sample honesty: n=5 is descriptive, not a calibrated estimate; one
success shifts the label. Recorded on every card by card_writer.

Usage: python -m pipeline.empirical_difficulty [--attempts 5]
       [--agent claude-code] [--model claude-haiku-4-5-20251001]
       [--n-concurrent 4] [--output artifacts/empirical_difficulty.json]
Exit 0 iff every task produced N scored attempts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from pipeline.common import (
    ARTIFACTS_DIR,
    ASSEMBLY_REPORT_PATH,
    CANDIDATES_DIR,
    GENERATED_TASKS_DIR,
    ROOT,
    funnel_log,
    utc_now_iso,
    write_json,
)

STAGE = "empirical_difficulty"
EMPIRICAL_PATH = ARTIFACTS_DIR / "empirical_difficulty.json"
JOBS_DIR = ROOT / "jobs"

DEFAULT_AGENT = "claude-code"          # confirmed in `harbor run --help` agent list
DEFAULT_MODEL = "claude-haiku-4-5-20251001"  # cheapest current Haiku-class model
KEYLESS_AGENTS = {"nop", "oracle"}

THRESHOLDS_NOTE = (
    "thresholds fixed in docs/PLAN.md (easy >=75%, medium 25-74%, hard <25%) "
    "before any empirical results were seen"
)


def empirical_label(solve_rate: float) -> str:
    if solve_rate >= 0.75:
        return "easy"
    if solve_rate >= 0.25:
        return "medium"
    return "hard"


def load_api_key() -> str | None:
    """Env first, then the gitignored .env. Never returns via print/log."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    env_file = ROOT / ".env"
    if not env_file.exists():
        return None
    ignored = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT)
    if ignored.returncode != 0:
        raise SystemExit(
            "REFUSING to read .env: it is NOT gitignored. Add `.env` to "
            ".gitignore before putting a key in it."
        )
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("ANTHROPIC_API_KEY="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def require_key(agent: str) -> dict[str, str]:
    """Return the env for the harbor subprocess; fail loudly if key missing."""
    if agent in KEYLESS_AGENTS:
        return dict(os.environ)
    key = load_api_key()
    if not key:
        raise SystemExit(
            "ANTHROPIC_API_KEY is not available.\n"
            "Provide it one of two ways (never commit it):\n"
            "  1) export ANTHROPIC_API_KEY=... in the shell running this, or\n"
            "  2) put ANTHROPIC_API_KEY=... in a gitignored .env at the repo root.\n"
            "Then re-run: uv run python -m pipeline.empirical_difficulty"
        )
    print(f"ANTHROPIC_API_KEY: set (length {len(key)}); value is never printed")
    return dict(os.environ) | {"ANTHROPIC_API_KEY": key}


def harbor_bin() -> str:
    found = shutil.which("harbor") or shutil.which(
        "harbor", path=str(Path.home() / ".local" / "bin"))
    if not found:
        raise SystemExit("harbor CLI not found (uv tool install harbor)")
    return found


def run_job(agent: str, model: str | None, attempts: int, n_concurrent: int,
            job_name: str, env: dict[str, str], timeout: float) -> Path:
    job_dir = JOBS_DIR / job_name
    if job_dir.exists():
        shutil.rmtree(job_dir)
    cmd = [
        harbor_bin(), "run",
        "-p", str(GENERATED_TASKS_DIR),
        "-a", agent,
        "-o", str(JOBS_DIR),
        "--job-name", job_name,
        "-k", str(attempts),        # attempts per task
        "-n", str(n_concurrent),    # concurrent trials (local Docker provider)
        "-q", "-y",
    ]
    if model:
        cmd += ["-m", model]
    res = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                         text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(
            f"harbor run exited {res.returncode}: "
            f"{(res.stderr or res.stdout).strip()[-800:]}"
        )
    return job_dir


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    t0 = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
    t1 = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
    return round((t1 - t0).total_seconds(), 1)


def parse_attempts(job_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """task_name -> one record per attempt, from per-trial result.json files."""
    by_task: dict[str, list[dict[str, Any]]] = {}
    for trial_result in sorted(job_dir.glob("*/result.json")):
        trial = json.loads(trial_result.read_text())
        exc = trial.get("exception_info") or {}
        exc_type = exc.get("exception_type")
        agent_result = trial.get("agent_result") or {}
        by_task.setdefault(trial["task_name"], []).append({
            "trial_name": trial["trial_name"],
            "reward": (trial.get("verifier_result") or {})
                      .get("rewards", {}).get("reward"),
            "wall_clock_seconds": _seconds(trial.get("started_at"),
                                           trial.get("finished_at")),
            "agent_seconds": _seconds(
                (trial.get("agent_execution") or {}).get("started_at"),
                (trial.get("agent_execution") or {}).get("finished_at")),
            "timed_out": "timeout" in (exc_type or "").lower(),
            "exception": exc_type,
            "cost_usd": agent_result.get("cost_usd"),
        })
    return by_task


def summarize(by_task: dict[str, list[dict[str, Any]]],
              tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for task in tasks:
        cid = task["candidate_id"]
        meta = json.loads((CANDIDATES_DIR / cid / "metadata.json").read_text())
        structural = meta["difficulty"]["structural_preliminary"]
        attempts = by_task.get(task["task_name"], [])
        successes = sum(1 for a in attempts if a["reward"] == 1.0)
        rate = round(successes / len(attempts), 3) if attempts else None
        label = empirical_label(rate) if rate is not None else None
        rows.append({
            "candidate_id": cid,
            "task_name": task["task_name"],
            "structural_label": structural,
            "n_attempts": len(attempts),
            "successes": successes,
            "solve_rate": rate,
            "empirical_label": label,
            "agreement": (label == structural) if label else None,
            "attempts": attempts,
        })
    return rows


def print_table(rows: list[dict[str, Any]]) -> None:
    print(f"\n{'task':<44} {'structural':>10} {'solve-rate':>10} "
          f"{'empirical':>10} {'agree':>6}")
    for r in rows:
        rate = f"{r['successes']}/{r['n_attempts']}" if r["n_attempts"] else "-"
        print(f"{r['task_name']:<44} {r['structural_label']:>10} "
              f"{rate:>10} {str(r['empirical_label']):>10} "
              f"{str(r['agreement']):>6}")
    print(f"\n({THRESHOLDS_NOTE})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--n-concurrent", type=int, default=4)
    parser.add_argument("--job-name", default="empdiff")
    parser.add_argument("--output", type=Path, default=EMPIRICAL_PATH)
    parser.add_argument("--harbor-timeout", type=float, default=7200.0)
    args = parser.parse_args(argv)

    model = None if args.agent in KEYLESS_AGENTS else args.model
    env = require_key(args.agent)
    os.environ["PATH"] = f"{Path.home() / '.local' / 'bin'}:{os.environ['PATH']}"
    env["PATH"] = os.environ["PATH"]

    assembly = json.loads(ASSEMBLY_REPORT_PATH.read_text())
    tasks = assembly["tasks"]
    if not tasks:
        print("no assembled tasks; run pipeline.assemble first", file=sys.stderr)
        return 1

    n_trials = len(tasks) * args.attempts
    print(f"running harbor job '{args.job_name}': agent={args.agent} "
          f"model={model} — {len(tasks)} task(s) x {args.attempts} attempt(s) "
          f"= {n_trials} trials, {args.n_concurrent} concurrent")
    job_dir = run_job(args.agent, model, args.attempts, args.n_concurrent,
                      args.job_name, env, args.harbor_timeout)

    by_task = parse_attempts(job_dir)
    rows = summarize(by_task, tasks)
    print_table(rows)

    complete = all(r["n_attempts"] == args.attempts for r in rows)
    for r in rows:
        funnel_log(STAGE, r["candidate_id"], "accept",
                   f"empirical={r['empirical_label']} "
                   f"({r['successes']}/{r['n_attempts']}), "
                   f"structural={r['structural_label']}, "
                   f"agree={r['agreement']}")

    total_cost = sum(a["cost_usd"] or 0
                     for r in rows for a in r["attempts"])
    write_json(args.output, {
        "generated_at": utc_now_iso(),
        "stage": STAGE,
        "agent": args.agent,
        "model": model,
        "attempts_per_task": args.attempts,
        "n_concurrent": args.n_concurrent,
        "job_dir": str(job_dir.relative_to(ROOT)),
        "thresholds": {"easy": ">=0.75", "medium": "0.25-0.74", "hard": "<0.25",
                       "note": THRESHOLDS_NOTE},
        "small_sample_caveat": (
            f"n={args.attempts} attempts per task — one success shifts the "
            "label; solve-rates are descriptive, not calibrated estimates"),
        "total_cost_usd": round(total_cost, 4) if total_cost else None,
        "results": rows,
    })
    print(f"wrote {args.output}"
          + (f" (total agent-reported cost: ${total_cost:.4f})"
             if total_cost else " (agent did not report costs)"))
    if not complete:
        print("WARNING: some tasks have missing attempts", file=sys.stderr)
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
