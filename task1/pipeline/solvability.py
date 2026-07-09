"""Stage 7 — Solvability gate.

Runs every assembled task through the real Harbor harness twice, using the
Stage-0 conventions (`harbor run -p` pointed at the PARENT dataset directory,
absolute path):

  oracle agent -> reward 1.0 required   (task is solvable by the known-good fix)
  nop    agent -> reward 0.0 required   (task starts unsolved; no free reward)

One Harbor job per agent covers all tasks (env images derive FROM the cached
Stage-1 seed image, so builds are cheap). Per-task rewards are parsed from the
job's result.json (reward buckets hold trial names '<task>__<suffix>').

Both rewards are recorded in the candidate's metadata.json; tasks failing
either requirement are rejected with reasons in funnel.jsonl.
Writes artifacts/solvability_report.json. Exit 0 iff every task passes both.

Usage: python -m pipeline.solvability [--config pipeline/seeds.toml]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from pipeline.common import (
    ASSEMBLY_REPORT_PATH,
    CANDIDATES_DIR,
    GENERATED_TASKS_DIR,
    ROOT,
    SOLVABILITY_REPORT_PATH,
    funnel_log,
    run,
    utc_now_iso,
    write_json,
)

STAGE = "solvability"
JOBS_DIR = ROOT / "jobs"
HARBOR_TIMEOUT = 1800  # one multi-task job, images cached; generous


def harbor_bin() -> str:
    found = shutil.which("harbor") or shutil.which(
        "harbor", path=str(Path.home() / ".local" / "bin"))
    if not found:
        raise SystemExit("harbor CLI not found (uv tool install harbor)")
    return found


def run_harbor_job(agent: str) -> Path:
    """Run one Harbor job for all generated tasks; return the job directory."""
    job_name = f"solv-{agent}"
    job_dir = JOBS_DIR / job_name
    if job_dir.exists():
        shutil.rmtree(job_dir)
    cmd = [
        harbor_bin(), "run",
        "-p", str(GENERATED_TASKS_DIR),   # PARENT dir, absolute (Stage 0 finding)
        "-a", agent,
        "-o", str(JOBS_DIR),
        "--job-name", job_name,
        "-q", "-y",
    ]
    res = run(cmd, timeout=HARBOR_TIMEOUT, cwd=ROOT)
    if res.returncode != 0:
        raise RuntimeError(
            f"harbor run -a {agent} exited {res.returncode}: "
            f"{(res.stderr or res.stdout).strip()[-800:]}"
        )
    return job_dir


def rewards_by_task(job_dir: Path) -> dict[str, dict[str, Any]]:
    """Map full task name -> {reward, error} by reading each trial's result.json.

    The job-level result.json buckets rewards by TRIAL name, which Harbor
    truncates — parsing it dropped '-L<line>' suffixes. Per-trial results carry
    the authoritative task_name and reward.
    """
    out: dict[str, dict[str, Any]] = {}
    for trial_result in sorted(job_dir.glob("*/result.json")):
        trial = json.loads(trial_result.read_text())
        task_name = trial["task_name"]
        exc = trial.get("exception_info")
        reward = (trial.get("verifier_result") or {}).get("rewards", {}).get("reward")
        out[task_name] = {
            "reward": reward,
            "error": (exc or {}).get("exception_type"),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.parse_args(argv)

    os.environ["PATH"] = f"{Path.home() / '.local' / 'bin'}:{os.environ['PATH']}"
    assembly = json.loads(ASSEMBLY_REPORT_PATH.read_text())
    tasks = assembly["tasks"]
    if not tasks:
        print("no assembled tasks; run pipeline.assemble first", file=sys.stderr)
        return 1

    results: dict[str, dict[str, dict[str, Any]]] = {}
    for agent in ("oracle", "nop"):
        print(f"running harbor job: agent={agent}, {len(tasks)} task(s) ...")
        job_dir = run_harbor_job(agent)
        results[agent] = rewards_by_task(job_dir)
        print(f"  rewards: { {k: v['reward'] for k, v in results[agent].items()} }")

    records = []
    n_pass = 0
    for task in tasks:
        cid = task["candidate_id"]
        oracle_res = results["oracle"].get(task["task_name"], {})
        nop_res = results["nop"].get(task["task_name"], {})
        oracle_reward = oracle_res.get("reward")
        nop_reward = nop_res.get("reward")
        oracle_ok = oracle_reward == 1.0
        nop_ok = nop_reward == 0.0
        verdict = "accept" if (oracle_ok and nop_ok) else "reject"
        reasons = []
        if not oracle_ok:
            reasons.append(f"oracle reward {oracle_reward!r} != 1.0"
                           + (f" (trial error: {oracle_res.get('error')})"
                              if oracle_res.get("error") else ""))
        if not nop_ok:
            reasons.append(f"nop reward {nop_reward!r} != 0.0"
                           + (f" (trial error: {nop_res.get('error')})"
                              if nop_res.get("error") else ""))
        reason = "; ".join(reasons) if reasons else \
            "oracle 1.0 and nop 0.0 through the real harness"
        funnel_log(STAGE, cid, verdict, reason,
                   detail={"oracle_reward": oracle_reward, "nop_reward": nop_reward})

        meta_path = CANDIDATES_DIR / cid / "metadata.json"
        meta = json.loads(meta_path.read_text())
        meta["solvability"] = {
            "verdict": verdict,
            "oracle_reward": oracle_reward,
            "nop_reward": nop_reward,
            "harness": "harbor run (agents: oracle, nop)",
            "checked_at": utc_now_iso(),
        }
        write_json(meta_path, meta)

        n_pass += verdict == "accept"
        records.append({
            "candidate_id": cid,
            "task_name": task["task_name"],
            "oracle_reward": oracle_reward,
            "nop_reward": nop_reward,
            "verdict": verdict,
            "reason": reason,
        })
        print(f"  {task['task_name']}: oracle={oracle_reward} nop={nop_reward} "
              f"-> {verdict.upper()}")

    write_json(SOLVABILITY_REPORT_PATH, {
        "generated_at": utc_now_iso(),
        "stage": STAGE,
        "n_tasks": len(records),
        "n_passed": n_pass,
        "results": records,
    })
    print(f"wrote {SOLVABILITY_REPORT_PATH}: {n_pass}/{len(records)} passed both gates")
    return 0 if n_pass == len(records) else 1


if __name__ == "__main__":
    sys.exit(main())
