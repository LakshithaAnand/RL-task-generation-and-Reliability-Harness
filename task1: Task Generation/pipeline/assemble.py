"""Stage 4 — Harbor assembly.

For each candidate that survived failure establishment, write a complete Harbor
task folder under tasks/generated/<task-name>/:

  task.toml        valid org/name (org "tbgen": invalid orgs are SILENTLY
                   skipped by Harbor discovery — Stage 0 finding)
  instruction.md   generated procedurally from template metadata (no LLM).
                   Difficulty knob: verbosity "explicit" (names the affected
                   function/area) or "symptom_only" (observable behavior only).
                   Never names the flipped tests, never gives steps.
  environment/     Dockerfile FROM the Stage-1 seed image; applies the mutation
                   at build; removes /repo/.git (or `git diff` would reveal the
                   defect) and the patch file in the same layer; replaces the
                   dev test dir with a copy PRUNED of the flipped tests.
  tests/           held-out verifier: full healthy test-dir copy + test.sh that
                   runs ONLY the flipped node IDs and writes 1/0 to
                   /logs/verifier/reward.txt.
  solution/        solve.sh with the oracle patch embedded as a heredoc
                   (independent of how much of solution/ the agent copies).

Also writes each task's structured spec (requirement <-> verifier-check mapping
from template metadata) to artifacts/candidates/<id>/spec.json, and an
assembly report to artifacts/assembly_report.json.

Usage: python -m pipeline.assemble [--config pipeline/seeds.toml]
Exit 0 iff every surviving candidate assembled cleanly.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

from pipeline.common import (
    ASSEMBLY_REPORT_PATH,
    CANDIDATES_DIR,
    ELIGIBILITY_REPORT_PATH,
    GENERATED_TASKS_DIR,
    Config,
    Seed,
    funnel_log,
    load_config,
    utc_now_iso,
    write_json,
)

STAGE = "assembly"


# --------------------------------------------------------------------------
# Held-out test pruning
# --------------------------------------------------------------------------

def flipped_by_file(flipped: list[str]) -> dict[str, set[str]]:
    """Group flipped node IDs by test file; strip parametrize suffixes.

    'test/test_output.py::test_grid[a]' -> {'test/test_output.py': {'test_grid'}}
    """
    grouped: dict[str, set[str]] = {}
    for node_id in flipped:
        path, _, rest = node_id.partition("::")
        func = rest.split("::")[-1].split("[")[0]
        grouped.setdefault(path, set()).add(func)
    return grouped


def prune_test_functions(source: str, names: set[str]) -> str:
    """Remove named test functions (incl. decorators) from a test file's text.

    Line-span deletion keeps every remaining byte identical (comments included).
    The result is re-parsed to prove it is still valid Python.
    """
    tree = ast.parse(source)
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            assert node.end_lineno is not None
            spans.append((first, node.end_lineno))
    if not spans:
        return source
    drop = {ln for a, b in spans for ln in range(a, b + 1)}
    kept = [l for i, l in enumerate(source.splitlines(keepends=True), start=1)
            if i not in drop]
    pruned = "".join(kept)
    ast.parse(pruned)  # raises if the deletion broke the file
    return pruned


# --------------------------------------------------------------------------
# Instruction generation (procedural; no LLM)
# --------------------------------------------------------------------------

def render_instruction(meta: dict[str, Any], verbosity: str) -> str:
    package = meta["repo"]
    symptom = meta["instruction_wording_template"].format(package=package)
    parts = [
        f"# Fix a defect in `{package}`\n",
        f"The Python library `{package}` (pretty-printing of tabular data) lives at "
        f"`/repo` and is installed into the environment's Python in editable mode, "
        f"so changes to `/repo` take effect immediately.\n",
        symptom + "\n",
    ]
    if verbosity == "explicit":
        parts.append(
            f"Where to look: the defect is in `{meta['file']}`, in or near "
            f"`{meta['enclosing_function']}()`.\n"
        )
    parts.append(
        "The repo's own developer tests under `/repo/test` currently pass and "
        "must still pass after your fix.\n"
    )
    parts.append(
        "Done looks like:\n"
        "- The defect is fixed at its source: a small change to the library "
        "code in `/repo`.\n"
        "- The library's behavior matches its documented/expected semantics "
        "for the affected inputs.\n"
        "- Correctness is judged by a held-out verifier that is not visible "
        "in this environment.\n"
    )
    return "\n".join(parts)


def leak_check(instruction: str, flipped: list[str]) -> list[str]:
    """Deterministic check: instruction must not name held-out tests or verifier paths."""
    leaks = []
    for node_id in flipped:
        func = node_id.partition("::")[2].split("::")[-1].split("[")[0]
        if func and func in instruction:
            leaks.append(func)
        fname = Path(node_id.partition("::")[0]).name
        if fname in instruction:
            leaks.append(fname)
    for marker in ("/tests/", "verifier_tests", "flipped"):
        if marker in instruction:
            leaks.append(marker)
    return sorted(set(leaks))


# --------------------------------------------------------------------------
# Task-folder writers
# --------------------------------------------------------------------------

_TASK_TOML = """\
schema_version = "1.3"
artifacts = []

[task]
name = "{org}/{task_name}"
description = "Fix a small introduced defect in the {repo} library so behavior matches its documented semantics."
keywords = ["defect-fix", "python", "{template}"]
[[task.authors]]
name = "tb-task-pipeline"
email = "laksaadarsh@gmail.com"

[metadata]
category = "software-engineering"
candidate_id = "{candidate_id}"
mutation_template = "{template}"
seed_repo = "{repo}"
seed_commit = "{commit}"
instruction_verbosity = "{verbosity}"
held_out_test_count = {n_flipped}
generator = "tb-task-pipeline (stages 1-4, deterministic, no LLM)"

[verifier]
timeout_sec = {verifier_timeout}
collect = []

[verifier.env]

[agent]
timeout_sec = {agent_timeout}

[environment]
# "no-network" would close the reinstall-healthy-package shortcut, but Harbor's
# local Docker provider cannot enforce it (rejects the task). Residual risk is
# documented and probed by the Stage-8 shortcut battery.
network_mode = "public"
build_timeout_sec = {build_timeout}
os = "linux"
mcp_servers = []

[environment.env]

[solution.env]
"""

_ENV_DOCKERFILE = """\
FROM {base_image}
# Apply the seeded defect at build time. /repo/.git is removed in the same
# layer: with it present, `git diff` inside the container would reveal the
# exact mutation. The patch file is removed too.
COPY broken.patch /tmp/broken.patch
RUN cd /repo && git apply /tmp/broken.patch \\
    && rm -rf /repo/.git /repo/{test_dir} /tmp/broken.patch
# Ordinary developer tests, EXCLUDING the held-out flipped tests.
COPY test/ /repo/{test_dir}/
"""

_TEST_SH = """\
#!/bin/bash
# Held-out verifier: runs ONLY the flipped regression tests (healthy-repo
# copies under /tests/verifier_tests) against the current /repo state.
# Reward contract: 1 -> /logs/verifier/reward.txt iff all held-out tests pass.
set -u
mkdir -p /logs/verifier
cd /repo
tr '\\n' '\\0' < /tests/flipped_tests.txt \\
  | xargs -0 timeout {inner_timeout} python -m pytest -q --tb=short -p no:cacheprovider
code=$?
if [ $code -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
echo "verifier pytest exit: $code"
"""

_SOLVE_SH = """\
#!/bin/bash
# Oracle solution: apply the inverse of the seeded mutation.
# The patch is embedded so this script is self-contained.
set -euo pipefail
cd /repo
git apply <<'__ORACLE_PATCH__'
{oracle_patch}
__ORACLE_PATCH__
echo "oracle patch applied"
"""


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_test_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def write_task(
    cfg: Config, seed: Seed, meta: dict[str, Any], verbosity: str
) -> dict[str, Any]:
    cid = meta["id"]
    flipped = meta["failure_establishment"]["flipped_tests"]
    task_name = f"{seed.name}-{cid}"
    full_name = f"{cfg.assembly.org}/{task_name}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", full_name):
        raise ValueError(f"task name {full_name!r} would be silently skipped by Harbor")

    task_dir = GENERATED_TASKS_DIR / task_name
    if task_dir.exists():
        shutil.rmtree(task_dir)
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "tests").mkdir()
    (task_dir / "solution").mkdir()

    cand_dir = CANDIDATES_DIR / cid
    test_src = seed.repo_dir / seed.test_dir

    # --- instruction.md (with leak check) ---------------------------------
    instruction = render_instruction(meta, verbosity)
    leaks = leak_check(instruction, flipped)
    if leaks:
        raise ValueError(f"instruction leaks held-out details: {leaks}")
    (task_dir / "instruction.md").write_text(instruction)

    # --- task.toml ---------------------------------------------------------
    (task_dir / "task.toml").write_text(_TASK_TOML.format(
        org=cfg.assembly.org,
        task_name=task_name,
        repo=seed.name,
        template=meta["template"],
        candidate_id=cid,
        commit=seed.commit,
        verbosity=verbosity,
        n_flipped=len(flipped),
        verifier_timeout=cfg.assembly.verifier_timeout_sec,
        agent_timeout=cfg.assembly.agent_timeout_sec,
        build_timeout=cfg.assembly.build_timeout_sec,
    ))

    # --- environment/: seed image + mutation + pruned dev tests ------------
    (task_dir / "environment" / "Dockerfile").write_text(_ENV_DOCKERFILE.format(
        base_image=seed.image_tag, test_dir=seed.test_dir))
    shutil.copyfile(cand_dir / "mutation.patch",
                    task_dir / "environment" / "broken.patch")
    env_tests = task_dir / "environment" / "test"
    _copy_test_tree(test_src, env_tests)
    for rel_file, names in flipped_by_file(flipped).items():
        pruned_path = env_tests / Path(rel_file).relative_to(seed.test_dir)
        pruned_path.write_text(prune_test_functions(pruned_path.read_text(), names))

    # --- tests/: held-out verifier ------------------------------------------
    _copy_test_tree(test_src, task_dir / "tests" / "verifier_tests")
    node_ids = [
        f"/tests/verifier_tests/{Path(n.partition('::')[0]).relative_to(seed.test_dir)}"
        f"::{n.partition('::')[2]}"
        for n in flipped
    ]
    (task_dir / "tests" / "flipped_tests.txt").write_text("\n".join(node_ids) + "\n")
    inner_timeout = int(cfg.assembly.verifier_timeout_sec) - 60
    test_sh = task_dir / "tests" / "test.sh"
    test_sh.write_text(_TEST_SH.format(inner_timeout=inner_timeout))
    _make_executable(test_sh)

    # --- solution/: oracle patch embedded in solve.sh -----------------------
    oracle_patch = (cand_dir / "oracle.patch").read_text().rstrip("\n")
    solve_sh = task_dir / "solution" / "solve.sh"
    solve_sh.write_text(_SOLVE_SH.format(oracle_patch=oracle_patch))
    _make_executable(solve_sh)

    # --- structured spec: requirement <-> verifier-check mapping ------------
    spec = {
        "candidate_id": cid,
        "task_name": full_name,
        "spec_source": "mutation template metadata (procedural; no LLM)",
        "requirements": [
            {
                "id": "R1",
                "text": meta["intended_behavior"],
                "origin": "template.intended_behavior",
            }
        ],
        "verifier_checks": [
            {
                "id": "V1",
                "kind": "held_out_flipped_regression_tests",
                "node_ids": node_ids,
                "execution": "tests/test.sh",
                "covers": ["R1"],
            }
        ],
        "coverage": {"R1": ["V1"]},
        "edge_cases_planned_stage5": meta["edge_cases"],
        "instruction": {"verbosity": verbosity, "leak_check": "passed"},
        "generated_at": utc_now_iso(),
    }
    write_json(cand_dir / "spec.json", spec)

    return {
        "candidate_id": cid,
        "task_name": full_name,
        "task_dir": str(task_dir.relative_to(GENERATED_TASKS_DIR.parent.parent)),
        "verbosity": verbosity,
        "n_held_out_tests": len(flipped),
    }


def load_survivors() -> list[dict[str, Any]]:
    metas = []
    for meta_path in sorted(CANDIDATES_DIR.glob("*/metadata.json")):
        meta = json.loads(meta_path.read_text())
        if meta.get("failure_establishment", {}).get("verdict") == "accept":
            metas.append(meta)
    return metas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    cfg = load_config(args.config) if args.config else load_config()

    report = json.loads(ELIGIBILITY_REPORT_PATH.read_text())
    eligible = [r["seed"] for r in report["seeds"] if r["eligible"]]
    seed = next(s for s in cfg.seeds if s.name == eligible[0])

    survivors = load_survivors()
    if not survivors:
        print("no surviving candidates; run earlier stages first", file=sys.stderr)
        return 1

    GENERATED_TASKS_DIR.mkdir(parents=True, exist_ok=True)
    cycle = cfg.assembly.verbosity_cycle
    records, failures = [], 0
    for i, meta in enumerate(survivors):
        verbosity = cycle[i % len(cycle)]
        try:
            rec = write_task(cfg, seed, meta, verbosity)
        except Exception as e:  # noqa: BLE001 - any assembly error is a reject
            funnel_log(STAGE, meta["id"], "reject", f"assembly failed: {e}")
            print(f"  {meta['id']}: REJECT ({e})")
            failures += 1
            continue
        funnel_log(STAGE, meta["id"], "accept",
                   f"assembled as {rec['task_name']} (verbosity={verbosity}, "
                   f"{rec['n_held_out_tests']} held-out tests)")
        print(f"  {meta['id']}: -> {rec['task_name']} "
              f"[{verbosity}, {rec['n_held_out_tests']} held-out]")
        records.append(rec)

    write_json(ASSEMBLY_REPORT_PATH, {
        "generated_at": utc_now_iso(),
        "stage": STAGE,
        "seed": seed.name,
        "base_image": seed.image_tag,
        "n_survivors_in": len(survivors),
        "n_assembled": len(records),
        "tasks": records,
    })
    print(f"wrote {ASSEMBLY_REPORT_PATH}: {len(records)}/{len(survivors)} assembled")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
