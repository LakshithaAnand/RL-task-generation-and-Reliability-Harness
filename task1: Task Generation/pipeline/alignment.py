"""Stage 6 — Instruction/verifier alignment check.

From each task's spec JSON (produced in Stage 4 and enriched in Stage 5),
confirm bidirectional traceability:

  - every requirement maps to >= 1 verifier check
  - every verifier check traces back to >= 1 stated requirement

Reject on uncovered requirements or unstated checks; log to funnel.jsonl.
This is evidence of alignment, not proof of perfect semantic coverage.

Usage: python -m pipeline.alignment [--config pipeline/seeds.toml]
Exit 0 iff every task is aligned.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pipeline.common import (
    ASSEMBLY_REPORT_PATH,
    CANDIDATES_DIR,
    ARTIFACTS_DIR,
    funnel_log,
    load_config,
    utc_now_iso,
    write_json,
)

STAGE = "alignment"
ALIGNMENT_REPORT_PATH = ARTIFACTS_DIR / "alignment_report.json"


def check_alignment(spec: dict[str, Any]) -> dict[str, Any]:
    req_ids = {r["id"] for r in spec["requirements"]}
    check_ids = {v["id"] for v in spec["verifier_checks"]}

    # requirement -> checks that claim to cover it
    covered: dict[str, list[str]] = {rid: [] for rid in req_ids}
    # check -> requirements it claims to cover
    check_covers: dict[str, list[str]] = {}
    for vc in spec["verifier_checks"]:
        covers = vc.get("covers", [])
        check_covers[vc["id"]] = covers
        for rid in covers:
            if rid in covered:
                covered[rid].append(vc["id"])

    uncovered_requirements = sorted(r for r, cs in covered.items() if not cs)
    # a check is "unstated" if it covers nothing, or references a missing requirement
    unstated_checks = sorted(
        vid for vid, covers in check_covers.items()
        if not covers or any(rid not in req_ids for rid in covers)
    )
    # cross-check the declared coverage map matches the per-check 'covers'
    declared = spec.get("coverage", {})
    coverage_map_consistent = all(
        sorted(declared.get(rid, [])) == sorted(covered[rid]) for rid in req_ids
    )

    aligned = (not uncovered_requirements and not unstated_checks
               and coverage_map_consistent)
    return {
        "aligned": aligned,
        "requirements": sorted(req_ids),
        "verifier_checks": sorted(check_ids),
        "coverage": covered,
        "uncovered_requirements": uncovered_requirements,
        "unstated_checks": unstated_checks,
        "coverage_map_consistent": coverage_map_consistent,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.parse_args(argv)

    assembly = json.loads(ASSEMBLY_REPORT_PATH.read_text())
    tasks = assembly["tasks"]
    if not tasks:
        print("no assembled tasks; run pipeline.assemble first", file=sys.stderr)
        return 1

    records, n_aligned = [], 0
    for task in tasks:
        cid = task["candidate_id"]
        spec_path = CANDIDATES_DIR / cid / "spec.json"
        spec = json.loads(spec_path.read_text())
        result = check_alignment(spec)

        verdict = "accept" if result["aligned"] else "reject"
        if result["aligned"]:
            reason = (f"{len(result['requirements'])} requirement(s) <-> "
                      f"{len(result['verifier_checks'])} check(s), fully traceable")
            n_aligned += 1
        else:
            problems = []
            if result["uncovered_requirements"]:
                problems.append(f"uncovered requirements {result['uncovered_requirements']}")
            if result["unstated_checks"]:
                problems.append(f"unstated checks {result['unstated_checks']}")
            if not result["coverage_map_consistent"]:
                problems.append("coverage map inconsistent with per-check 'covers'")
            reason = "; ".join(problems)
        funnel_log(STAGE, cid, verdict, reason)

        # record in spec + metadata
        spec["alignment"] = {"verdict": verdict, "reason": reason,
                             "checked_at": utc_now_iso()}
        write_json(spec_path, spec)
        meta_path = CANDIDATES_DIR / cid / "metadata.json"
        meta = json.loads(meta_path.read_text())
        meta["alignment"] = {"verdict": verdict, "reason": reason,
                             "checked_at": utc_now_iso()}
        write_json(meta_path, meta)

        print(f"  {cid}: {verdict.upper()} — {reason}")
        records.append({"candidate_id": cid, "task_name": task["task_name"],
                        "verdict": verdict, "reason": reason, **result})

    write_json(ALIGNMENT_REPORT_PATH, {
        "generated_at": utc_now_iso(),
        "stage": STAGE,
        "n_tasks": len(records),
        "n_aligned": n_aligned,
        "results": records,
    })
    print(f"wrote {ALIGNMENT_REPORT_PATH}: {n_aligned}/{len(records)} aligned")
    return 0 if n_aligned == len(records) else 1


if __name__ == "__main__":
    sys.exit(main())
