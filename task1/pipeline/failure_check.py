"""Stage 3 — Failure establishment.

For each candidate, in a fresh container from the Stage-1 image:
  1. (once per run, not per candidate) confirm the healthy suite is green
  2. apply mutation.patch, rerun the suite, require >=1 previously-passing test
     to fail cleanly (pytest exit 1, not a collection/internal error)
  3. apply oracle.patch and require the suite green again (round-trip proof)

Records WHICH tests flipped in each candidate's metadata.json. Discards (no flip,
suite error, failed round-trip, patch trouble) are logged to funnel.jsonl.
Writes artifacts/failure_report.json. Exit 0 iff >=1 candidate survives.

Usage: python -m pipeline.failure_check [--config pipeline/seeds.toml]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from pipeline.common import (
    CANDIDATES_DIR,
    ELIGIBILITY_REPORT_PATH,
    FAILURE_REPORT_PATH,
    Seed,
    docker_run,
    funnel_log,
    load_config,
    parse_failed_tests,
    parse_pytest_summary,
    run,
    utc_now_iso,
    write_json,
)

STAGE = "failure_establishment"

PYTEST = "python -m pytest -q --tb=no -rf -p no:cacheprovider"

# Mutations can introduce infinite loops (observed: a while-loop boundary flip
# hung the suite), so every in-container pytest run gets a hard timeout.
# `timeout` exits 124 on expiry, which we classify as a distinct reject reason.
TIMEOUT_EXIT = 124


def _candidate_script(per_run_timeout: int) -> str:
    pytest_cmd = f"timeout {per_run_timeout} {PYTEST}"
    # One container run per candidate: apply -> test -> revert via oracle -> test.
    # Markers delimit sections so the host parses each pytest run separately.
    # printf '\n...' guards the markers: a timed-out pytest is killed mid-line,
    # which would otherwise glue the marker onto the progress-dot line.
    return f"""\
set -u
cd /repo
if ! git apply /cand/mutation.patch; then echo "::apply_mutation=failed"; exit 0; fi
echo "::mutated_run_begin"
{pytest_cmd}
printf '\\n::mutated_exit=%s\\n' "$?"
if ! git apply /cand/oracle.patch; then echo "::apply_oracle=failed"; exit 0; fi
echo "::oracle_run_begin"
{pytest_cmd}
printf '\\n::oracle_exit=%s\\n' "$?"
"""

# Not ^-anchored: belt-and-braces against markers ending up mid-line.
_MUTATED_EXIT_RE = re.compile(r"::mutated_exit=(\d+)")
_ORACLE_EXIT_RE = re.compile(r"::oracle_exit=(\d+)")


def check_baseline_green(seed: Seed, timeout: float) -> dict[str, Any]:
    res = docker_run(seed.image_tag, PYTEST, timeout=timeout)
    summary = parse_pytest_summary(res.stdout)
    return {"ok": res.returncode == 0, "exit_code": res.returncode, **summary}


def _section(output: str, begin_marker: str, end_re: re.Pattern[str]) -> str:
    start = output.find(begin_marker)
    if start == -1:
        return ""
    rest = output[start + len(begin_marker):]
    m = end_re.search(rest)
    return rest[: m.start()] if m else rest


def evaluate_candidate(
    seed: Seed, cand_dir: Path, timeout: float, per_run_timeout: int
) -> dict[str, Any]:
    cid = cand_dir.name
    container_name = f"tb-fc-{cid}"
    try:
        res = docker_run(
            seed.image_tag,
            _candidate_script(per_run_timeout),
            timeout=timeout,
            ro_mounts={cand_dir: "/cand"},
            name=container_name,
        )
    except subprocess.TimeoutExpired:
        # Backstop only; the in-container `timeout` should fire first.
        run(["docker", "rm", "-f", container_name], timeout=60)
        return {"id": cid, "verdict": "reject",
                "reason": f"container run exceeded host timeout {timeout}s"}
    out = res.stdout

    result: dict[str, Any] = {"id": cid}
    if "::apply_mutation=failed" in out:
        result.update(verdict="reject", reason="mutation patch failed to apply")
        return result

    mutated_exit_m = _MUTATED_EXIT_RE.search(out)
    if mutated_exit_m is None:
        result.update(verdict="reject",
                      reason=f"container run incomplete (exit {res.returncode})")
        return result
    mutated_exit = int(mutated_exit_m.group(1))
    mutated_out = _section(out, "::mutated_run_begin", _MUTATED_EXIT_RE)
    flipped = parse_failed_tests(mutated_out)
    result["mutated_suite"] = {
        "exit_code": mutated_exit,
        **parse_pytest_summary(mutated_out),
    }

    if mutated_exit == 0:
        result.update(verdict="reject", reason="no test flipped (suite still green)")
        return result
    if mutated_exit == TIMEOUT_EXIT:
        result.update(
            verdict="reject",
            reason="mutated suite timed out (mutation likely introduced an "
                   "infinite loop; a hang is not a clean pass->fail flip)",
        )
        return result
    if mutated_exit != 1:
        result.update(
            verdict="reject",
            reason=f"suite did not fail cleanly (pytest exit {mutated_exit}, "
                   "collection/internal error rather than test failures)",
        )
        return result
    if not flipped:
        result.update(verdict="reject",
                      reason="pytest exit 1 but no FAILED tests parsed")
        return result
    result["flipped_tests"] = flipped

    if "::apply_oracle=failed" in out:
        result.update(verdict="reject", reason="oracle patch failed to apply")
        return result
    oracle_exit_m = _ORACLE_EXIT_RE.search(out)
    oracle_exit = int(oracle_exit_m.group(1)) if oracle_exit_m else None
    oracle_out = _section(out, "::oracle_run_begin", _ORACLE_EXIT_RE)
    result["oracle_suite"] = {
        "exit_code": oracle_exit,
        **parse_pytest_summary(oracle_out),
    }
    if oracle_exit != 0:
        result.update(
            verdict="reject",
            reason=f"round-trip failed: suite not green after oracle patch "
                   f"(exit {oracle_exit})",
        )
        return result

    result.update(verdict="accept",
                  reason=f"{len(flipped)} test(s) flipped pass->fail; "
                         "oracle round-trip green")
    return result


def _record_in_metadata(cand_dir: Path, result: dict[str, Any]) -> None:
    meta_path = cand_dir / "metadata.json"
    meta = json.loads(meta_path.read_text())
    meta["failure_establishment"] = {
        "verdict": result["verdict"],
        "reason": result["reason"],
        "flipped_tests": result.get("flipped_tests", []),
        "mutated_suite": result.get("mutated_suite"),
        "oracle_suite": result.get("oracle_suite"),
        "checked_at": utc_now_iso(),
    }
    write_json(meta_path, meta)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    cfg = load_config(args.config) if args.config else load_config()

    report = json.loads(ELIGIBILITY_REPORT_PATH.read_text())
    eligible = [r["seed"] for r in report["seeds"] if r["eligible"]]
    if not eligible:
        print("no eligible seeds; run pipeline.eligibility first", file=sys.stderr)
        return 1
    seed = next(s for s in cfg.seeds if s.name == eligible[0])
    timeout = cfg.limits.container_run_timeout_seconds
    per_run_timeout = int(cfg.limits.max_test_seconds)

    baseline = check_baseline_green(seed, timeout)
    if not baseline["ok"]:
        print(f"healthy baseline not green: {baseline}", file=sys.stderr)
        return 1
    print(f"baseline green: {baseline['counts']} in {baseline['duration_seconds']}s")

    cand_dirs = sorted(p for p in CANDIDATES_DIR.iterdir() if p.is_dir())
    results = []
    for cand_dir in cand_dirs:
        result = evaluate_candidate(seed, cand_dir, timeout, per_run_timeout)
        _record_in_metadata(cand_dir, result)
        funnel_log(STAGE, result["id"], result["verdict"], result["reason"],
                   detail={"flipped_tests": result.get("flipped_tests", [])} or None)
        n_flip = len(result.get("flipped_tests", []))
        print(f"  {result['id']}: {result['verdict'].upper()} "
              f"({result['reason']}; flips={n_flip})")
        results.append(result)

    survivors = [r["id"] for r in results if r["verdict"] == "accept"]
    out = {
        "generated_at": utc_now_iso(),
        "stage": STAGE,
        "seed": seed.name,
        "image": seed.image_tag,
        "baseline": baseline,
        "n_candidates": len(results),
        "n_survivors": len(survivors),
        "survivors": survivors,
        "results": results,
    }
    write_json(FAILURE_REPORT_PATH, out)
    print(f"wrote {FAILURE_REPORT_PATH}: {len(survivors)}/{len(results)} survived")
    return 0 if survivors else 1


if __name__ == "__main__":
    sys.exit(main())
