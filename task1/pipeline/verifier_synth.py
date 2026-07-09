"""Stage 5 — Verifier synthesis.

Strengthen each assembled task's held-out verifier:

  (a) regression copies of the flipped tests            (already present; confirmed)
  (b) template-driven edge-case tests, generated mechanically from the mutation
      metadata:
        boundary_flip      -> below/at/above-boundary probes (row/col counts,
                              text-wrap widths) on the mutated comparison
        inverted_condition -> true-branch / false-branch probes (alignments,
                              table formats that hit the branch)
      Each probe is a characterization test: call the public tabulate() API,
      capture the ORACLE-state output as the golden value.
  (c) ADMISSION RULE: every synthesized test must FAIL on the broken state and
      PASS on the oracle state, in-container, or it is discarded and logged.
      (This proves compatibility with the task, not semantic validity — a
      stated limitation; the golden value is taken from the oracle, not proven.)

Admitted tests are written under the task's tests/edge/ (held out; never in the
agent environment). Provenance is labeled in the task spec JSON:
regression_copy vs template_generated.

Usage: python -m pipeline.verifier_synth [--config pipeline/seeds.toml]
Exit 0 always (a task with zero admitted edge tests keeps its regression verifier).
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

from pipeline.common import (
    ASSEMBLY_REPORT_PATH,
    CANDIDATES_DIR,
    GENERATED_TASKS_DIR,
    build_task_image,
    docker_run,
    funnel_log,
    load_config,
    utc_now_iso,
    write_json,
)

STAGE = "verifier_synthesis"


# --------------------------------------------------------------------------
# Template-driven edge-probe pools (public tabulate() API; mechanical).
# Each probe is a Python expression over T = tabulate.tabulate. The admission
# rule keeps only those whose output differs between broken and oracle.
# --------------------------------------------------------------------------

_BOUNDARY_PROBES: list[dict[str, str]] = [
    # row/column count around the 0/1 boundary
    {"id": "rows0_headers", "edge": "at_boundary", "code": 'T([], headers=["a", "b"])'},
    {"id": "rows0_bare", "edge": "at_boundary", "code": "T([])"},
    {"id": "rows1", "edge": "above_boundary", "code": 'T([[1, 2]], headers=["a", "b"])'},
    {"id": "rows2", "edge": "above_boundary", "code": "T([[1, 2], [3, 4]])"},
    {"id": "cols1", "edge": "above_boundary", "code": "T([[1], [2]])"},
    {"id": "single_value", "edge": "at_boundary", "code": "T([[42]])"},
    {"id": "firstrow_only", "edge": "at_boundary",
     "code": 'T([["a", "b"]], headers="firstrow")'},
    {"id": "empty_string_cell", "edge": "at_boundary", "code": 'T([[""]])'},
    # text-wrap width boundary (maxcolwidths / long words)
    {"id": "wrap_exact", "edge": "at_boundary", "code": 'T([["abcdef"]], maxcolwidths=[3])'},
    {"id": "wrap_words", "edge": "above_boundary",
     "code": 'T([["a bb ccc dddd"]], maxcolwidths=[4])'},
    {"id": "wrap_longword_grid", "edge": "above_boundary",
     "code": 'T([["verylongword"]], maxcolwidths=[5], tablefmt="grid")'},
    {"id": "wrap_width1", "edge": "below_boundary",
     "code": 'T([["abc"]], maxcolwidths=[1])'},
]

_CONDITION_PROBES: list[dict[str, str]] = [
    # alignment branches
    {"id": "align_left", "edge": "condition_true_path",
     "code": 'T([[1, 2]], colalign=("left", "left"))'},
    {"id": "align_right", "edge": "condition_false_path",
     "code": 'T([[1, 2]], colalign=("right", "right"))'},
    {"id": "align_center", "edge": "condition_false_path",
     "code": 'T([[1, 2]], colalign=("center", "center"))'},
    {"id": "align_decimal", "edge": "condition_false_path",
     "code": 'T([[1.5], [2.25]], colalign=("decimal",))'},
    # pipe format with colon-alignment markers (hits _pipe_segment_with_colons)
    {"id": "pipe_left_right", "edge": "condition_true_path",
     "code": 'T([[1, 2]], tablefmt="pipe", colalign=("left", "right"))'},
    {"id": "pipe_center", "edge": "condition_false_path",
     "code": 'T([["a"]], headers=["h"], tablefmt="pipe", colalign=("center",))'},
    {"id": "pipe_default", "edge": "condition_true_path",
     "code": 'T([[1, 2]], headers=["a", "b"], tablefmt="pipe")'},
    # html / unsafehtml branches
    {"id": "html", "edge": "condition_true_path", "code": 'T([[1]], tablefmt="html")'},
    {"id": "unsafehtml", "edge": "condition_false_path",
     "code": 'T([["<b>x</b>"]], tablefmt="unsafehtml")'},
    {"id": "html_headers", "edge": "condition_true_path",
     "code": 'T([[1, 2]], headers=["a", "b"], tablefmt="html")'},
    # a spread of table formats to exercise format-selection branches
    {"id": "fmt_github", "edge": "condition_false_path",
     "code": 'T([[1, 2]], headers=["a", "b"], tablefmt="github")'},
    {"id": "fmt_rst", "edge": "condition_false_path",
     "code": 'T([[1, 2]], headers=["a", "b"], tablefmt="rst")'},
    {"id": "fmt_grid", "edge": "condition_true_path",
     "code": 'T([[1, 2]], headers=["a", "b"], tablefmt="grid")'},
]

_POOLS = {"boundary_flip": _BOUNDARY_PROBES, "inverted_condition": _CONDITION_PROBES}

_CAPTURE_PY = """\
import json, tabulate
T = tabulate.tabulate
specs = json.load(open("/caps/specs.json"))
out = {}
for s in specs:
    try:
        out[s["id"]] = {"ok": True, "val": eval(s["code"], {"tabulate": tabulate, "T": T})}
    except Exception as e:  # noqa: BLE001
        out[s["id"]] = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:200]}"}
print(json.dumps(out))
"""


def capture_states(image: str, cand_dir: Path, specs: list[dict[str, str]],
                   timeout: float) -> tuple[dict, dict]:
    """Return (broken_outputs, oracle_outputs) for the probe specs, in-container."""
    caps = cand_dir / "_caps"
    caps.mkdir(exist_ok=True)
    (caps / "specs.json").write_text(json.dumps(specs))
    (caps / "capture.py").write_text(_CAPTURE_PY)
    script = (
        "set -e\n"
        "python /caps/capture.py > /tmp/broken.json\n"
        "cd /repo && git apply /cand/oracle.patch\n"
        "python /caps/capture.py > /tmp/oracle.json\n"
        'echo "::BROKEN::"; cat /tmp/broken.json\n'
        'echo "::ORACLE::"; cat /tmp/oracle.json\n'
    )
    res = docker_run(image, script, timeout=timeout,
                     ro_mounts={cand_dir: "/cand", caps: "/caps"})
    if "::BROKEN::" not in res.stdout or "::ORACLE::" not in res.stdout:
        raise RuntimeError(f"capture failed: {(res.stderr or res.stdout)[-500:]}")
    broken_raw = res.stdout.split("::BROKEN::", 1)[1].split("::ORACLE::", 1)[0]
    oracle_raw = res.stdout.split("::ORACLE::", 1)[1]
    return json.loads(broken_raw), json.loads(oracle_raw)


def admit(specs, broken, oracle) -> tuple[list[dict], list[dict]]:
    """Apply the admission rule. Returns (admitted, discarded-with-reason)."""
    admitted, discarded = [], []
    for spec in specs:
        b, o = broken[spec["id"]], oracle[spec["id"]]
        if not o["ok"]:
            discarded.append({**spec, "reason": f"oracle errored: {o['err']}"})
            continue
        distinguishes = (not b["ok"]) or (b["val"] != o["val"])
        if not distinguishes:
            discarded.append({**spec,
                              "reason": "broken output == oracle output (does not "
                                        "reveal the defect)"})
            continue
        admitted.append({**spec, "golden": o["val"]})
    return admitted, discarded


def render_edge_test_file(template: str, admitted: list[dict]) -> str:
    lines = [
        '"""Template-generated edge-case verifier tests (Stage 5).',
        "",
        "Provenance: template_generated. Each test is a characterization test:",
        "the expected value is the ORACLE-state output of the public tabulate()",
        "API for a template-relevant edge input. Admitted only after proving it",
        "fails on the broken state and passes on the oracle state in-container.",
        '"""',
        "import tabulate",
        "",
        "T = tabulate.tabulate",
        "",
    ]
    for spec in admitted:
        lines.append(f"def test_edge_{template}_{spec['id']}():")
        lines.append(f"    # edge: {spec['edge']}")
        lines.append(f"    assert ({spec['code']}) == {spec['golden']!r}")
        lines.append("")
    return "\n".join(lines)


_TEST_SH = """\
#!/bin/bash
# Held-out verifier (Stage 5 synthesis): flipped regression tests (regression_copy)
# PLUS admitted template-generated edge tests. Reward 1 iff ALL pass.
set -u
mkdir -p /logs/verifier
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


def confirm_regression(task_dir: Path) -> dict[str, Any]:
    flipped = (task_dir / "tests" / "flipped_tests.txt")
    vt = task_dir / "tests" / "verifier_tests"
    node_ids = [l for l in flipped.read_text().splitlines() if l.strip()]
    ok = flipped.exists() and vt.is_dir() and len(node_ids) > 0
    return {"ok": ok, "n_regression_tests": len(node_ids), "node_ids": node_ids}


def synthesize_for_task(cfg, task: dict[str, Any]) -> dict[str, Any]:
    cid = task["candidate_id"]
    task_dir = GENERATED_TASKS_DIR / Path(task["task_dir"]).name
    cand_dir = CANDIDATES_DIR / cid
    meta = json.loads((cand_dir / "metadata.json").read_text())
    template = meta["template"]

    regression = confirm_regression(task_dir)
    specs = _POOLS[template]
    image = build_task_image(task_dir)
    broken, oracle = capture_states(image, cand_dir, specs, timeout=300)
    admitted, discarded = admit(specs, broken, oracle)

    # write admitted edge tests (held out under tests/edge/)
    edge_dir = task_dir / "tests" / "edge"
    if edge_dir.exists():
        for f in edge_dir.glob("*.py"):
            f.unlink()
    edge_node_ids: list[str] = []
    if admitted:
        edge_dir.mkdir(exist_ok=True)
        edge_file = edge_dir / f"test_edge_{template}.py"
        edge_file.write_text(render_edge_test_file(template, admitted))
        ast.parse(edge_file.read_text())  # generated file must parse
        edge_node_ids = [
            f"/tests/edge/{edge_file.name}::test_edge_{template}_{s['id']}"
            for s in admitted
        ]
        # refresh test.sh to also run the edge tests
        inner = int(cfg.assembly.verifier_timeout_sec) - 60
        test_sh = task_dir / "tests" / "test.sh"
        test_sh.write_text(_TEST_SH.format(inner_timeout=inner))
        import stat as _stat
        test_sh.chmod(test_sh.stat().st_mode | _stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)

    # update spec.json: provenance labels + edge verifier checks
    spec_path = cand_dir / "spec.json"
    spec = json.loads(spec_path.read_text())
    for vc in spec["verifier_checks"]:
        if vc["id"] == "V1":
            vc["provenance"] = "regression_copy"
    if admitted:
        spec["verifier_checks"].append({
            "id": "V2",
            "kind": "template_generated_edge_tests",
            "provenance": "template_generated",
            "node_ids": edge_node_ids,
            "execution": "tests/test.sh",
            "covers": ["R1"],
            "per_test": [{"id": s["id"], "edge": s["edge"], "code": s["code"]}
                         for s in admitted],
        })
        spec["coverage"]["R1"] = sorted(set(spec["coverage"]["R1"] + ["V2"]))
    spec["verifier_synthesis"] = {
        "regression_copies_confirmed": regression["ok"],
        "n_admitted": len(admitted),
        "n_discarded": len(discarded),
        "admission_rule": "fail-on-broken AND pass-on-oracle, in-container",
        "caveat": "characterization tests: golden taken from oracle, proves "
                  "compatibility not semantic validity",
        "updated_at": utc_now_iso(),
    }
    write_json(spec_path, spec)

    # record in metadata + funnel
    meta["verifier_synthesis"] = {
        "template": template,
        "regression_confirmed": regression["ok"],
        "n_regression_tests": regression["n_regression_tests"],
        "n_probes": len(specs),
        "n_admitted": len(admitted),
        "n_discarded": len(discarded),
        "admitted_ids": [s["id"] for s in admitted],
        "checked_at": utc_now_iso(),
    }
    write_json(cand_dir / "metadata.json", meta)
    for s in admitted:
        funnel_log(STAGE, f"{cid}:edge:{s['id']}", "accept",
                   f"admitted template_generated edge test ({s['edge']})")
    for d in discarded:
        funnel_log(STAGE, f"{cid}:edge:{d['id']}", "reject", d["reason"])

    # cleanup capture scratch
    caps = cand_dir / "_caps"
    for f in caps.glob("*"):
        f.unlink()
    caps.rmdir()

    print(f"  {cid} [{template}]: regression={'ok' if regression['ok'] else 'MISSING'} "
          f"({regression['n_regression_tests']}), edge admitted "
          f"{len(admitted)}/{len(specs)} (discarded {len(discarded)})")
    return {
        "candidate_id": cid,
        "template": template,
        "regression_confirmed": regression["ok"],
        "n_admitted": len(admitted),
        "n_discarded": len(discarded),
        "admitted_ids": [s["id"] for s in admitted],
    }


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

    records = [synthesize_for_task(cfg, t) for t in tasks]
    write_json(ARTIFACTS_REPORT := (ASSEMBLY_REPORT_PATH.parent / "verifier_synth_report.json"), {
        "generated_at": utc_now_iso(),
        "stage": STAGE,
        "n_tasks": len(records),
        "total_admitted": sum(r["n_admitted"] for r in records),
        "total_discarded": sum(r["n_discarded"] for r in records),
        "results": records,
    })
    print(f"wrote {ARTIFACTS_REPORT}: "
          f"{sum(r['n_admitted'] for r in records)} edge tests admitted across "
          f"{len(records)} tasks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
