"""Stage 8, Part 2 — Near-miss battery.

For each task I (the pipeline author) hand-write 3-4 task-specific *almost-right*
solution patches — plausible fixes a capable agent might submit that are NOT
correct — spanning the categories: partial-fix / no-op cleanup, off-by-one,
and assertion-gaming (force a branch). Each is committed under
artifacts/nearmiss/<task>/ labelled `llm_proposed` in provenance.json.

A near-miss is materialised from the task's own oracle patch: same location and
context, but the replacement line is the near-miss instead of the true fix — so
every patch is guaranteed to apply to the broken state at the right spot.

A deterministic runner applies each in a fresh container and runs the held-out
verifier:
  REJECT  reward 0  -> the verifier caught the almost-right solution (good)
  ACCEPT  reward 1  -> the near-miss SLIPPED THROUGH (a finding to report and,
                      once, try to harden via a Stage-5 edge test)

Per-task rejection rate is reported honestly. Slips are not hidden.

Usage: python -m pipeline.nearmiss [--config pipeline/seeds.toml]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pipeline.common import (
    ARTIFACTS_DIR,
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
from pipeline.verifier_synth import _POOLS, capture_states, render_edge_test_file

STAGE = "integrity_nearmiss"
NEARMISS_DIR = ARTIFACTS_DIR / "nearmiss"
NEARMISS_REPORT_PATH = ARTIFACTS_DIR / "nearmiss_report.json"

# Author-written near-misses, keyed by task folder. `new_line` replaces the
# single changed line (indentation included) that the oracle patch fixes.
NEAR_MISSES: dict[str, list[dict[str, str]]] = {
    "tabulate-c02-boundary_flip-L2446": [
        {"id": "off_by_one", "category": "off_by_one",
         "new_line": "        if len(self._active_codes) > 1:",
         "desc": "off-by-one: reset omitted when exactly one code is active"},
        {"id": "wrong_variant", "category": "partial_fix",
         "new_line": "        if len(self._active_codes) < 0:",
         "desc": "wrong direction: reset never emitted"},
        {"id": "unfixed", "category": "partial_fix",
         "new_line": "        if len(self._active_codes) >= 0:",
         "desc": "no-op: agent claims a fix but leaves the buggy comparison"},
    ],
    "tabulate-c03-boundary_flip-L1458": [
        {"id": "off_by_one", "category": "off_by_one",
         "new_line": "    if headers == \"firstrow\" and len(rows) > 1:",
         "desc": "off-by-one: single-row firstrow tables mishandled"},
        {"id": "wrong_variant", "category": "partial_fix",
         "new_line": "    if headers == \"firstrow\" and len(rows) < 0:",
         "desc": "wrong direction: firstrow branch never taken"},
        {"id": "unfixed", "category": "partial_fix",
         "new_line": "    if headers == \"firstrow\" and len(rows) >= 0:",
         "desc": "no-op: leaves the buggy comparison"},
    ],
    "tabulate-c05-inverted_condition-L121": [
        {"id": "partial_noop", "category": "partial_fix",
         "new_line": "        elif align != \"left\":",
         "desc": "cosmetic rewrite equivalent to the bug (not fixed)"},
        {"id": "assert_true", "category": "assertion_gaming",
         "new_line": "        elif True:",
         "desc": "force the left branch for every alignment"},
        {"id": "assert_false", "category": "assertion_gaming",
         "new_line": "        elif False:",
         "desc": "never take the left branch"},
    ],
    "tabulate-c06-inverted_condition-L846": [
        {"id": "wrong_variant", "category": "partial_fix",
         "new_line": "    if _isconvertible(float, string):",
         "desc": "inverts the guard the wrong way"},
        {"id": "assert_true", "category": "assertion_gaming",
         "new_line": "    if True:",
         "desc": "treat every string as non-number-eligible"},
        {"id": "assert_false", "category": "assertion_gaming",
         "new_line": "    if False:",
         "desc": "skip the guard entirely"},
    ],
    "tabulate-c07-inverted_condition-L2393": [
        {"id": "partial_noop", "category": "partial_fix",
         "new_line": "        if fmt.lineabove != _html_begin_table_without_header:",
         "desc": "cosmetic rewrite equivalent to the bug (not fixed)"},
        {"id": "assert_true", "category": "assertion_gaming",
         "new_line": "        if True:",
         "desc": "always wrap output as JupyterHTMLStr"},
        {"id": "assert_false", "category": "assertion_gaming",
         "new_line": "        if False:",
         "desc": "never wrap output as JupyterHTMLStr"},
    ],
}


def materialize_patch(oracle_patch: str, new_line: str) -> tuple[str, str, str]:
    """Build a broken->near-miss patch from the oracle patch by swapping the
    single added (`+`) line for `new_line`. Returns (patch, broken_line, fix_line)."""
    out, broken_line, fix_line = [], None, None
    for line in oracle_patch.splitlines(keepends=True):
        if line.startswith("+++") or line.startswith("---"):
            out.append(line)
        elif line.startswith("+"):
            fix_line = line[1:].rstrip("\n")
            out.append("+" + new_line + "\n")
        else:
            if line.startswith("-") and not line.startswith("---"):
                broken_line = line[1:].rstrip("\n")
            out.append(line)
    return "".join(out), broken_line or "", fix_line or ""


def materialize_all() -> dict[str, list[dict[str, Any]]]:
    """Write near-miss patch files + provenance for every task."""
    assembly = json.loads(ASSEMBLY_REPORT_PATH.read_text())
    by_task: dict[str, list[dict[str, Any]]] = {}
    for task in assembly["tasks"]:
        folder = Path(task["task_dir"]).name
        cid = task["candidate_id"]
        oracle_patch = (CANDIDATES_DIR / cid / "oracle.patch").read_text()
        nm_dir = NEARMISS_DIR / folder
        nm_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        for nm in NEAR_MISSES[folder]:
            patch, broken_line, fix_line = materialize_patch(oracle_patch, nm["new_line"])
            # sanity: near-miss must differ from the true fix
            assert nm["new_line"].strip() != fix_line.strip(), \
                f"{folder}/{nm['id']} equals the real fix"
            (nm_dir / f"{nm['id']}.patch").write_text(patch)
            entries.append({**nm, "patch_file": f"{nm['id']}.patch",
                            "broken_line": broken_line, "true_fix_line": fix_line})
        write_json(nm_dir / "provenance.json", {
            "task": task["task_name"],
            "provenance": "llm_proposed",
            "note": "author-written almost-right solutions; harness decides accept/reject",
            "near_misses": entries,
        })
        by_task[folder] = entries
    return by_task


_RUN_ONE = """
set -u
cd /repo
if ! git apply /nm/{patch_file}; then echo "APPLY_FAILED"; exit 0; fi
bash /tests/test.sh >/tmp/v.log 2>&1
echo "REWARD=$(cat /logs/verifier/reward.txt)"
"""


def run_near_miss(image, task_dir, nm_dir, patch_file, timeout=420) -> dict[str, Any]:
    out = docker_run(image, _RUN_ONE.format(patch_file=patch_file), timeout=timeout,
                     ro_mounts={task_dir / "tests": "/tests", nm_dir: "/nm"}).stdout
    if "APPLY_FAILED" in out:
        return {"reward": None, "verdict": "error", "detail": "patch did not apply"}
    reward = next((l.split("=", 1)[1].strip() for l in out.splitlines()
                   if l.startswith("REWARD=")), "?")
    if reward in ("0", "0.0"):
        return {"reward": reward, "verdict": "REJECT"}
    if reward in ("1", "1.0"):
        return {"reward": reward, "verdict": "ACCEPT"}  # slip!
    return {"reward": reward, "verdict": "error", "detail": f"unexpected reward {reward!r}"}


def try_harden_slip(cfg, task, folder, cid, slipped: list[str]) -> dict[str, Any]:
    """One hardening attempt: find a probe that distinguishes each slipped
    near-miss from the oracle (fails broken+nearmiss, passes oracle) and admit
    it as a Stage-5 edge test."""
    cand_dir = CANDIDATES_DIR / cid
    task_dir = GENERATED_TASKS_DIR / folder
    meta = json.loads((cand_dir / "metadata.json").read_text())
    template = meta["template"]
    image = build_task_image(task_dir)
    specs = _POOLS[template]
    broken, oracle = capture_states(image, cand_dir, specs, timeout=300)

    # capture near-miss states too
    admitted_new = []
    for nm_id in slipped:
        nm_patch = NEARMISS_DIR / folder / f"{nm_id}.patch"
        # run capture with the near-miss applied instead of the oracle
        _, nm_state = _capture_with_patch(image, cand_dir, specs, nm_patch)
        for spec in specs:
            o, n = oracle[spec["id"]], nm_state[spec["id"]]
            if not o["ok"]:
                continue
            # A hardening test only needs to PASS on oracle and FAIL on the
            # near-miss. It need NOT fail on broken: the regression tests already
            # force broken->0, and a near-miss can differ from the oracle exactly
            # where the broken state happens to agree with the oracle.
            fails_nm = (not n["ok"]) or n["val"] != o["val"]
            if fails_nm:
                admitted_new.append({**spec, "golden": o["val"], "targets": nm_id})
                break
    if not admitted_new:
        return {"hardened": False, "reason": "no distinguishing edge test found"}

    # append to the task's edge test file
    edge_dir = task_dir / "tests" / "edge"
    edge_dir.mkdir(exist_ok=True)
    src = render_edge_test_file(template + "_hardening", admitted_new)
    (edge_dir / f"test_edge_{template}_hardening.py").write_text(src)
    return {"hardened": True, "n_new_edge_tests": len(admitted_new),
            "admitted_ids": [s["id"] for s in admitted_new]}


def _capture_with_patch(image, cand_dir, specs, patch_path):
    """Capture probe outputs on broken and on (broken+patch) states."""
    caps = cand_dir / "_caps_nm"
    caps.mkdir(exist_ok=True)
    (caps / "specs.json").write_text(json.dumps(specs))
    (caps / "capture.py").write_text(
        'import json,tabulate\nT=tabulate.tabulate\n'
        's=json.load(open("/caps/specs.json"));o={}\n'
        'for x in s:\n'
        ' try:o[x["id"]]={"ok":True,"val":eval(x["code"],{"tabulate":tabulate,"T":T})}\n'
        ' except Exception as e:o[x["id"]]={"ok":False,"err":str(e)[:120]}\n'
        'print(json.dumps(o))\n')
    script = (
        "python /caps/capture.py > /tmp/b.json\n"
        f"cd /repo && git apply /nm/{patch_path.name}\n"
        "python /caps/capture.py > /tmp/p.json\n"
        'echo "::B::";cat /tmp/b.json;echo;echo "::P::";cat /tmp/p.json\n'
    )
    out = docker_run(image, script, timeout=300,
                     ro_mounts={caps: "/caps", patch_path.parent: "/nm"}).stdout
    # note: mounts a whole dir; ensure patch filename is 'patch'
    b = json.loads(out.split("::B::", 1)[1].split("::P::", 1)[0])
    p = json.loads(out.split("::P::", 1)[1])
    for f in caps.glob("*"):
        f.unlink()
    caps.rmdir()
    return b, p


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    cfg = load_config(args.config) if args.config else load_config()

    materialize_all()
    assembly = json.loads(ASSEMBLY_REPORT_PATH.read_text())

    all_results, total, total_reject = {}, 0, 0
    for task in assembly["tasks"]:
        folder = Path(task["task_dir"]).name
        cid = task["candidate_id"]
        task_dir = GENERATED_TASKS_DIR / folder
        nm_dir = NEARMISS_DIR / folder
        image = build_task_image(task_dir)
        print(f"near-miss: {task['task_name']}")

        results = []
        for nm in NEAR_MISSES[folder]:
            r = run_near_miss(image, task_dir, nm_dir, f"{nm['id']}.patch")
            results.append({**nm, **r})
            print(f"    {nm['id']:16} [{nm['category']:16}] {r['verdict']}"
                  f" (reward={r.get('reward')})")

        slipped = [r["id"] for r in results if r["verdict"] == "ACCEPT"]
        hardening = None
        if slipped:
            print(f"  SLIP(S): {slipped} -> attempting one edge-test hardening")
            hardening = try_harden_slip(cfg, task, folder, cid, slipped)
            if hardening.get("hardened"):
                from pipeline.hardening import reverify_solvable_property
                image = build_task_image(task_dir)
                prop = reverify_solvable_property(image, task_dir, CANDIDATES_DIR / cid)
                hardening["post_harden_solvable_property"] = prop
                print(f"    post-harden broken/oracle: {prop['ok']} "
                      f"(broken={prop['broken_reward']}, oracle={prop['oracle_reward']})")
                for r in results:
                    if r["verdict"] == "ACCEPT":
                        r2 = run_near_miss(image, task_dir, nm_dir, f"{r['id']}.patch")
                        r["verdict_after_hardening"] = r2["verdict"]
                        r["reward_after_hardening"] = r2.get("reward")
                        print(f"    re-run {r['id']}: {r2['verdict']} (was ACCEPT)")
            else:
                print(f"    hardening not possible: {hardening.get('reason')}")

        n = len(results)
        n_reject = sum(1 for r in results
                       if (r.get("verdict_after_hardening") or r["verdict"]) == "REJECT")
        total += n
        total_reject += n_reject
        for r in results:
            final = r.get("verdict_after_hardening") or r["verdict"]
            funnel_log(STAGE, f"{cid}:nearmiss:{r['id']}",
                       "accept" if final == "REJECT" else "reject",
                       f"{r['category']}: verifier {final}"
                       + (" (after hardening)" if r.get("verdict_after_hardening") else ""))

        meta = json.loads((cand_dir := CANDIDATES_DIR / cid).joinpath("metadata.json").read_text())
        meta.setdefault("integrity", {})["near_miss_battery"] = {
            "n": n, "n_reject": n_reject,
            "rejection_rate": round(n_reject / n, 3) if n else None,
            "results": results, "hardening": hardening,
            "checked_at": utc_now_iso(),
        }
        write_json(cand_dir / "metadata.json", meta)
        all_results[task["task_name"]] = {
            "n": n, "n_reject": n_reject,
            "rejection_rate": round(n_reject / n, 3) if n else None,
            "results": results, "hardening": hardening,
        }

    write_json(NEARMISS_REPORT_PATH, {
        "generated_at": utc_now_iso(),
        "stage": STAGE,
        "categories": sorted({nm["category"] for v in NEAR_MISSES.values() for nm in v}),
        "total_near_misses": total,
        "total_rejected": total_reject,
        "overall_rejection_rate": round(total_reject / total, 3) if total else None,
        "results": all_results,
    })
    print(f"wrote {NEARMISS_REPORT_PATH}: {total_reject}/{total} rejected "
          f"({round(100*total_reject/total)}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
