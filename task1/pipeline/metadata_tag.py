"""Stage 9 — Difficulty + diversity metadata.

Difficulty here is STRUCTURAL and PRELIMINARY: a label (easy/medium/hard)
computed from deterministic, model-free signals, each recorded next to the
label so a reader can audit exactly why a task got its tag:

  flipped_test_count       fewer pre-existing tests flip -> less of the suite
                           points at the defect -> harder to localize
  instruction_verbosity    symptom_only (no file/function hint) is harder
                           than explicit
  verifier_test_count      a small held-out verifier (regression copies +
                           admitted edge tests + hardening edge tests) means a
                           narrow behavioral surface exposes the defect
  call_graph_depth         approximate intra-module call depth from the seed's
                           public API functions to the mutated site's enclosing
                           function (cheap AST BFS; None if the healthy clone
                           is absent or the site is unreachable through simple
                           Name/Attribute call edges — then it scores 0 and is
                           reported as unavailable)

Card caveat (verbatim, attached to every label):
  "empirical solve-rate anchoring is the designed next step, not yet run."
No empirical solve rates are computed or implied anywhere in this stage.

Diversity: each task is tagged category / skill_type / difficulty and a
coverage table shows the set's spread. Stated honestly: all current tasks
share one seed repo.

Usage: python -m pipeline.metadata_tag [--config pipeline/seeds.toml]
Exit 0 iff every assembled task was tagged.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any

from pipeline.common import (
    ARTIFACTS_DIR,
    ASSEMBLY_REPORT_PATH,
    CANDIDATES_DIR,
    REPOS_DIR,
    funnel_log,
    load_config,
    utc_now_iso,
    write_json,
)

STAGE = "metadata_tag"
METADATA_REPORT_PATH = ARTIFACTS_DIR / "metadata_report.json"

DIFFICULTY_CAVEAT = "empirical solve-rate anchoring is the designed next step, not yet run."

SKILL_TYPES = {
    "boundary_flip": "boundary-condition-reasoning",
    "inverted_condition": "control-flow-reasoning",
}

# Scoring rules (thresholds are heuristic, fixed here before any empirical
# anchoring; the breakdown ships on the card so the label is auditable).
#   flipped_test_count : <=4 -> 2 pts, 5-19 -> 1 pt, >=20 -> 0 pts
#   verbosity          : symptom_only -> 1 pt, explicit -> 0 pts
#   verifier_test_count: <=5 -> 1 pt, else 0 pts
#   call_graph_depth   : >=2 -> 1 pt, 0/1 or unavailable -> 0 pts
#   label              : total <=1 easy, 2-3 medium, >=4 hard


def _points_flipped(n: int) -> int:
    return 2 if n <= 4 else (1 if n < 20 else 0)


def _points_verbosity(verbosity: str) -> int:
    return 1 if verbosity == "symptom_only" else 0


def _points_verifier(n: int) -> int:
    return 1 if n <= 5 else 0


def _points_depth(depth: int | None) -> int:
    return 1 if depth is not None and depth >= 2 else 0


def _label(total: int) -> str:
    return "easy" if total <= 1 else ("medium" if total <= 3 else "hard")


def call_graph_depth(source: str, target_function: str) -> int | None:
    """Approximate min call depth from any public module-level function to
    `target_function`, using intra-module Name/Attribute call edges only.

    Cheap by design (one AST walk + BFS). Returns None if unreachable.
    """
    tree = ast.parse(source)
    defs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs[node.name] = node
    if target_function not in defs:
        return None

    calls: dict[str, set[str]] = {}
    for name, fn in defs.items():
        callees: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    callees.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    callees.add(node.func.attr)
        calls[name] = callees & set(defs)

    entries = [n for n, fn in defs.items()
               if not n.startswith("_") and fn.col_offset == 0]
    if target_function in entries:
        return 0
    seen = set(entries)
    queue = deque((e, 0) for e in entries)
    while queue:
        name, depth = queue.popleft()
        for callee in sorted(calls.get(name, ())):
            if callee == target_function:
                return depth + 1
            if callee not in seen:
                seen.add(callee)
                queue.append((callee, depth + 1))
    return None


def compute_signals(meta: dict[str, Any], verbosity: str,
                    repo_dir: Path) -> list[dict[str, Any]]:
    n_flipped = len(meta["failure_establishment"]["flipped_tests"])
    synth = meta.get("verifier_synthesis", {})
    hardening = (meta.get("integrity", {}).get("near_miss_battery", {})
                 .get("hardening") or {})
    n_verifier = (synth.get("n_regression_tests", 0)
                  + synth.get("n_admitted", 0)
                  + (hardening.get("n_new_edge_tests", 0) if hardening.get("hardened") else 0))

    source_path = repo_dir / meta["file"]
    if source_path.exists():
        depth = call_graph_depth(source_path.read_text(), meta["enclosing_function"])
        depth_note = ("approximate intra-module call depth from public API"
                      if depth is not None
                      else "site unreachable via simple call edges; scored 0")
    else:
        depth = None
        depth_note = "healthy clone absent; not computed; scored 0"

    return [
        {"name": "flipped_test_count", "value": n_flipped,
         "points": _points_flipped(n_flipped),
         "note": "fewer flipped tests -> harder to localize"},
        {"name": "instruction_verbosity", "value": verbosity,
         "points": _points_verbosity(verbosity),
         "note": "symptom_only gives no file/function hint"},
        {"name": "verifier_test_count", "value": n_verifier,
         "points": _points_verifier(n_verifier),
         "note": "regression copies + admitted edge + hardening edge tests"},
        {"name": "call_graph_depth", "value": depth,
         "points": _points_depth(depth), "note": depth_note},
    ]


def tag_task(task: dict[str, Any], repo_dir: Path) -> dict[str, Any]:
    cid = task["candidate_id"]
    meta_path = CANDIDATES_DIR / cid / "metadata.json"
    meta = json.loads(meta_path.read_text())

    signals = compute_signals(meta, task["verbosity"], repo_dir)
    total = sum(s["points"] for s in signals)
    label = _label(total)

    difficulty = {
        "structural_preliminary": label,
        "total_points": total,
        "signals": signals,
        "caveat": DIFFICULTY_CAVEAT,
        "empirical_solve_rate": None,
    }
    diversity = {
        "category": "software-engineering",
        "skill_type": SKILL_TYPES[meta["template"]],
        "repo": meta["repo"],
    }
    meta["difficulty"] = {**difficulty, "tagged_at": utc_now_iso()}
    meta["diversity"] = diversity
    write_json(meta_path, meta)

    funnel_log(STAGE, cid, "accept",
               f"difficulty={label} ({total} pts: "
               + ", ".join(f"{s['name']}={s['value']}:{s['points']}" for s in signals)
               + f"); skill={diversity['skill_type']}")
    return {"candidate_id": cid, "task_name": task["task_name"],
            "difficulty": difficulty, "diversity": diversity}


def coverage_table(records: list[dict[str, Any]]) -> dict[str, Any]:
    cells: dict[str, dict[str, int]] = {}
    repos: dict[str, int] = {}
    for r in records:
        skill = r["diversity"]["skill_type"]
        label = r["difficulty"]["structural_preliminary"]
        cells.setdefault(skill, {})[label] = cells.get(skill, {}).get(label, 0) + 1
        repos[r["diversity"]["repo"]] = repos.get(r["diversity"]["repo"], 0) + 1
    occupied = sum(1 for row in cells.values() for v in row.values() if v)
    return {
        "by_skill_and_difficulty": cells,
        "occupied_cells": occupied,
        "clustered_in_one_cell": occupied <= 1,
        "repos": repos,
        "single_repo_note": (
            f"all {len(records)} task(s) share one seed repo "
            f"({', '.join(sorted(repos))}); cross-repo diversity is not yet "
            "demonstrated — a stated limitation, not an oversight"
            if len(repos) == 1 else None
        ),
    }


def print_coverage(coverage: dict[str, Any]) -> None:
    labels = ["easy", "medium", "hard"]
    cells = coverage["by_skill_and_difficulty"]
    width = max(len(s) for s in cells) if cells else 10
    print(f"  {'skill_type':<{width}}  " + "  ".join(f"{l:>6}" for l in labels))
    for skill in sorted(cells):
        row = cells[skill]
        print(f"  {skill:<{width}}  "
              + "  ".join(f"{row.get(l, 0):>6}" for l in labels))
    print(f"  occupied cells: {coverage['occupied_cells']}"
          f" (clustered_in_one_cell={coverage['clustered_in_one_cell']})")
    if coverage["single_repo_note"]:
        print(f"  NOTE: {coverage['single_repo_note']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    cfg = load_config(args.config) if args.config else load_config()

    assembly = json.loads(ASSEMBLY_REPORT_PATH.read_text())
    tasks = assembly["tasks"]
    if not tasks:
        print("no assembled tasks; run pipeline.assemble first", file=sys.stderr)
        return 1
    seed = next(s for s in cfg.seeds if s.name == assembly["seed"])

    records = []
    for task in tasks:
        rec = tag_task(task, seed.repo_dir)
        d = rec["difficulty"]
        print(f"  {rec['candidate_id']}: {d['structural_preliminary']} "
              f"({d['total_points']} pts) "
              + ", ".join(f"{s['name']}={s['value']}({s['points']})"
                          for s in d["signals"]))
        records.append(rec)

    coverage = coverage_table(records)
    print("coverage:")
    print_coverage(coverage)

    write_json(METADATA_REPORT_PATH, {
        "generated_at": utc_now_iso(),
        "stage": STAGE,
        "difficulty_rules": {
            "flipped_test_count": "<=4 -> 2 pts; 5-19 -> 1 pt; >=20 -> 0 pts",
            "instruction_verbosity": "symptom_only -> 1 pt; explicit -> 0 pts",
            "verifier_test_count": "<=5 -> 1 pt; else 0 pts",
            "call_graph_depth": ">=2 -> 1 pt; 0/1 or unavailable -> 0 pts",
            "label": "total <=1 easy; 2-3 medium; >=4 hard",
        },
        "caveat": DIFFICULTY_CAVEAT,
        "tasks": records,
        "coverage": coverage,
    })
    print(f"wrote {METADATA_REPORT_PATH}: {len(records)} task(s) tagged")
    return 0 if len(records) == len(tasks) else 1


if __name__ == "__main__":
    sys.exit(main())
