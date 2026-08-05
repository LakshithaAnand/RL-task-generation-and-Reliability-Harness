"""Stage 10b — Task Assurance Cards.

One machine-readable JSON card per accepted task, aggregating EVERY gate's
evidence from the artifacts earlier stages already wrote (candidate
metadata.json, spec.json, near-miss provenance, the hardening registry):

  provenance, failure establishment, solvability rewards, spec coverage +
  alignment, verifier-synthesis admissions/discards with provenance labels,
  shortcut-battery per-probe status INCLUDING the harden-loop history and the
  registry version, the near-miss table with residuals explicit, the
  independent handcheck note, structural difficulty with its caveat, and the
  residual-risk list.

The card is validated against schemas/assurance_card.schema.json (top-level
and per-section required keys; no external deps) and written to BOTH the task
folder (tasks/generated/<task>/assurance_card.json — outside environment/, so
the agent never sees it) and artifacts/cards/.

Usage: python -m pipeline.card_writer [--config pipeline/seeds.toml]
Exit 0 iff every assembled task got a schema-valid card.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
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
from pipeline.registry import load_registry

STAGE = "assurance_card"
CARDS_DIR = ARTIFACTS_DIR / "cards"
NEARMISS_DIR = ARTIFACTS_DIR / "nearmiss"
SCHEMA_PATH = ROOT / "schemas" / "assurance_card.schema.json"
EMPIRICAL_PATH = ARTIFACTS_DIR / "empirical_difficulty.json"

# Claims the standalone scripts/handcheck.sh re-verifies, by task folder.
# handcheck.sh shares no code with the Stage-8 battery; it rebuilds the task
# image and re-executes the exploit / near-miss by hand-followable commands.
_HANDCHECK_CLAIMS: dict[str, list[str]] = {
    "tabulate-c02-boundary_flip-L2446": [
        "spoof_reward_json is CLOSED: planted reward.json+reward.txt (=1.0), "
        "ran the real verifier, Harbor-style resolution returned 0.0",
    ],
    "tabulate-c07-inverted_condition-L2393": [
        "sanity leg: untouched broken repo scores 0.0",
        "assert_true residual is OPEN: committed near-miss patch applied, "
        "verifier returned 1.0 — the honest residual is real, not a battery artifact",
    ],
}
_HANDCHECK_NOTE = (
    "scripts/handcheck.sh is a standalone re-verification: it shares no code "
    "with pipeline/integrity.py, nearmiss.py, or hardening.py, prints every "
    "command before running it, and can be re-run with `bash scripts/handcheck.sh`."
)


def validate_against_schema(card: dict[str, Any],
                            schema: dict[str, Any]) -> list[str]:
    """Minimal structural validation: required keys at the top level and one
    level down, per the committed schema. Returns a list of problems."""
    problems = [f"missing top-level key: {key}"
                for key in schema["required"] if key not in card]
    for key, sub in schema.get("properties", {}).items():
        if key not in card or "required" not in sub:
            continue
        if not isinstance(card[key], dict):
            continue
        problems += [f"{key}: missing key {k}"
                     for k in sub["required"] if k not in card[key]]
    if "residual_risks" in card and not card["residual_risks"]:
        problems.append("residual_risks must not be empty (no task is risk-free)")
    return problems


def _near_miss_table(battery: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    table, residual_open = [], []
    for r in battery.get("results", []):
        final = r.get("verdict_after_hardening") or r["verdict"]
        row = {
            "id": r["id"],
            "category": r["category"],
            "description": r["desc"],
            "verdict_initial": r["verdict"],
            "reward_initial": r.get("reward"),
            "final_verdict": final,
            "residual_open": final == "ACCEPT",
        }
        if "verdict_after_hardening" in r:
            row["verdict_after_hardening"] = r["verdict_after_hardening"]
            row["reward_after_hardening"] = r.get("reward_after_hardening")
        table.append(row)
        if row["residual_open"]:
            residual_open.append(r["id"])
    return table, residual_open


def _shortcut_probes(battery: dict[str, Any],
                     registry: dict[str, Any]) -> dict[str, Any]:
    guarded_by = {e["guarding_probe"]: e["id"] for e in registry["entries"]}
    probes = {}
    for name, r in battery["results"].items():
        entry: dict[str, Any] = {
            "status": r["status"],
            "premise": r.get("premise"),
            "detail": r.get("detail"),
        }
        if r.get("hardened"):
            entry["harden_loop"] = {
                "status_before_hardening": r.get("status_before_hardening"),
                "hardening_applied": r.get("hardening_applied"),
                "registry_entry": guarded_by.get(name),
            }
        elif name in guarded_by:
            entry["registry_entry"] = guarded_by[name]
        probes[name] = entry
    return probes


def _load_empirical() -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    """(run-level record, candidate_id -> per-task row) or (None, {})."""
    if not EMPIRICAL_PATH.exists():
        return None, {}
    emp = json.loads(EMPIRICAL_PATH.read_text())
    return emp, {r["candidate_id"]: r for r in emp["results"]}


def _empirical_caveat(attempts: int) -> str:
    return (f"empirical anchoring run with n={attempts} attempts — "
            "small-sample caveat: one success shifts the label; solve-rates "
            "are descriptive, not calibrated estimates.")


def _difficulty_block(meta: dict[str, Any], emp_run: dict[str, Any] | None,
                      emp_row: dict[str, Any] | None) -> dict[str, Any]:
    block = {
        "structural_preliminary": meta["difficulty"]["structural_preliminary"],
        "total_points": meta["difficulty"]["total_points"],
        "signals": meta["difficulty"]["signals"],
        "caveat": meta["difficulty"]["caveat"],
        "empirical_solve_rate": None,
    }
    if emp_run and emp_row:
        block["caveat"] = _empirical_caveat(emp_run["attempts_per_task"])
        block["empirical_solve_rate"] = emp_row["solve_rate"]
        block["empirical"] = {
            "agent": emp_run["agent"],
            "model": emp_run["model"],
            "attempts": emp_row["n_attempts"],
            "successes": emp_row["successes"],
            "solve_rate": emp_row["solve_rate"],
            "label": emp_row["empirical_label"],
            "per_attempt_rewards": [a["reward"] for a in emp_row["attempts"]],
            "thresholds": emp_run["thresholds"],
            "run_at": emp_run["generated_at"],
        }
        block["agreement"] = {
            "structural_label": emp_row["structural_label"],
            "empirical_label": emp_row["empirical_label"],
            "agree": emp_row["agreement"],
            "note": "disagreements recorded, not reconciled",
        }
    return block


def _residual_risks(folder: str, spec: dict[str, Any], meta: dict[str, Any],
                    network_mode: str, residual_open: list[str],
                    nm_by_id: dict[str, dict[str, Any]],
                    emp_run: dict[str, Any] | None) -> list[str]:
    if emp_run:
        difficulty_risk = (
            f"Difficulty is anchored on n={emp_run['attempts_per_task']} "
            f"attempts by one model ({emp_run['model']}); one success shifts "
            "the label — descriptive, not a calibrated estimate.")
    else:
        difficulty_risk = ("Difficulty is structural_preliminary only; "
                           + meta["difficulty"]["caveat"])
    risks = [
        "The oracle is a known-good solution (the inverse of the mutation), "
        "not proven unique; other correct fixes exist and would also pass.",
        "Near-miss rejection is measured over a stated, author-written set "
        "(labeled llm_proposed); same-family generation has correlated blind "
        "spots — novel cheat families remain unprobed.",
        difficulty_risk,
        "All current tasks derive from a single seed repo (tabulate); "
        "set-level diversity across repos is not yet demonstrated.",
    ]
    if meta["verifier_synthesis"]["n_admitted"] > 0:
        risks.insert(1, (
            "Admitted edge tests are characterization tests: golden values are "
            "taken from the oracle state, so admission proves compatibility "
            "with the task, not semantic validity."))
    if network_mode == "public":
        risks.append(
            "environment.network_mode='public' (Harbor's local Docker provider "
            "rejects 'no-network'): the pip_reinstall shortcut is probed and "
            "blocked, but other network routes are unprobed.")
    for nm_id in residual_open:
        nm = nm_by_id.get(nm_id, {})
        line = (f"OPEN RESIDUAL: near-miss '{nm_id}' "
                f"({nm.get('category', '?')}: {nm.get('desc', '?')}) is accepted "
                f"by the verifier (reward 1) without restoring the original "
                f"condition; the one-shot hardening attempt found no "
                f"distinguishing edge test.")
        if folder in _HANDCHECK_CLAIMS:
            line += " Independently confirmed open by scripts/handcheck.sh."
        risks.append(line)
    return risks


def build_card(task: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    folder = Path(task["task_dir"]).name
    cid = task["candidate_id"]
    cand_dir = CANDIDATES_DIR / cid
    meta = json.loads((cand_dir / "metadata.json").read_text())
    spec = json.loads((cand_dir / "spec.json").read_text())
    task_toml = tomllib.loads((GENERATED_TASKS_DIR / folder / "task.toml").read_text())
    network_mode = task_toml["environment"]["network_mode"]

    nm_provenance = json.loads((NEARMISS_DIR / folder / "provenance.json").read_text())
    nm_by_id = {nm["id"]: nm for nm in nm_provenance["near_misses"]}
    nm_battery = meta["integrity"]["near_miss_battery"]
    nm_table, residual_open = _near_miss_table(nm_battery)

    sc_battery = meta["integrity"]["shortcut_battery"]
    synth = meta["verifier_synthesis"]
    emp_run, emp_by_cid = _load_empirical()
    emp_row = emp_by_cid.get(cid)

    return {
        "card_version": 1,
        "generated_at": utc_now_iso(),
        "task": {
            "name": task["task_name"],
            "folder": f"tasks/generated/{folder}",
            "instruction_verbosity": task["verbosity"],
        },
        "provenance": {
            "seed_repo": meta["repo"],
            "seed_commit": meta["commit"],
            "mutation_template": meta["template"],
            "template_description": meta["template_description"],
            "rng_seed": meta["rng_seed"],
            "candidate_id": cid,
            "file": meta["file"],
            "line": meta["line"],
            "enclosing_function": meta["enclosing_function"],
            "changed_condition": meta["changed_condition"],
            "source_sha256_healthy": meta["source_sha256_healthy"],
            "source_sha256_mutated": meta["source_sha256_mutated"],
            "generator": task_toml["metadata"]["generator"],
        },
        "failure_establishment": {
            "verdict": meta["failure_establishment"]["verdict"],
            "n_flipped_tests": len(meta["failure_establishment"]["flipped_tests"]),
            "flipped_tests": meta["failure_establishment"]["flipped_tests"],
            "oracle_round_trip_green": True,
            "checked_at": meta["failure_establishment"]["checked_at"],
        },
        "solvability": {
            **meta["solvability"],
        },
        "spec_coverage": {
            "spec_source": spec["spec_source"],
            "requirements": spec["requirements"],
            "verifier_checks": [
                {"id": vc["id"], "kind": vc["kind"],
                 "provenance": vc.get("provenance"),
                 "covers": vc["covers"], "n_tests": len(vc.get("node_ids", []))}
                for vc in spec["verifier_checks"]
            ],
            "coverage": spec["coverage"],
            "alignment": spec["alignment"],
        },
        "verifier_synthesis": {
            "n_probes": synth["n_probes"],
            "n_admitted": synth["n_admitted"],
            "n_discarded": synth["n_discarded"],
            "admitted_ids": synth["admitted_ids"],
            "admission_rule": spec["verifier_synthesis"]["admission_rule"],
            "provenance_labels": {
                "regression_copy": synth["n_regression_tests"],
                "template_generated": synth["n_admitted"],
                "llm_proposed": 0,
            },
            "caveat": spec["verifier_synthesis"]["caveat"],
        },
        "shortcut_battery": {
            "registry_version": sc_battery.get("registry_version",
                                               registry["version"]),
            "checked_at": sc_battery["checked_at"],
            "probes": _shortcut_probes(sc_battery, registry),
            "post_harden_solvable_property":
                sc_battery.get("post_harden_solvable_property"),
        },
        "near_miss_battery": {
            "provenance": "llm_proposed (author-written, committed under "
                          "artifacts/nearmiss/; re-applied and re-scored "
                          "deterministically by the harness)",
            "n": nm_battery["n"],
            "n_reject": nm_battery["n_reject"],
            "rejection_rate": nm_battery["rejection_rate"],
            "table": nm_table,
            "residual_open_ids": residual_open,
            "hardening": nm_battery.get("hardening"),
            "checked_at": nm_battery["checked_at"],
        },
        "independent_handcheck": {
            "covered": folder in _HANDCHECK_CLAIMS,
            "script": "scripts/handcheck.sh",
            "claims_reverified": _HANDCHECK_CLAIMS.get(folder, []),
            "note": _HANDCHECK_NOTE if folder in _HANDCHECK_CLAIMS else
                    "this task is not covered by the standalone handcheck; its "
                    "evidence comes from the Stage-8 battery only",
        },
        "difficulty": _difficulty_block(meta, emp_run, emp_row),
        "diversity": meta["diversity"],
        "evidence_provenance": {
            "live": [
                "failure establishment (suite run in-container)",
                "solvability rewards (real `harbor run`, oracle + nop agents)",
                "verifier-synthesis admission (fail-broken/pass-oracle, in-container)",
                "alignment traceability check",
                "shortcut battery incl. harden-loop re-probe",
                "near-miss re-apply + re-score",
                "difficulty signals (deterministic, from artifacts + AST)",
            ],
            "precomputed_or_authored": [
                "near-miss patches (author-written, committed, labeled llm_proposed)",
                "hardening registry entries (authored from battery findings)",
                "handcheck claims (standalone script, re-runnable on demand)",
            ] + ([
                "empirical difficulty solve-rates (model-dependent, generated "
                "once by pipeline/empirical_difficulty.py with an API key; the "
                "zero-key demo re-reads the committed artifact)"
            ] if emp_run else []),
        },
        "residual_risks": _residual_risks(folder, spec, meta, network_mode,
                                          residual_open, nm_by_id, emp_run),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.parse_args(argv)

    registry = load_registry()
    schema = json.loads(SCHEMA_PATH.read_text())
    assembly = json.loads(ASSEMBLY_REPORT_PATH.read_text())
    tasks = assembly["tasks"]
    if not tasks:
        print("no assembled tasks; run pipeline.assemble first", file=sys.stderr)
        return 1

    n_ok = 0
    for task in tasks:
        folder = Path(task["task_dir"]).name
        card = build_card(task, registry)
        problems = validate_against_schema(card, schema)
        if problems:
            funnel_log(STAGE, task["candidate_id"], "reject",
                       f"card failed schema check: {problems}")
            print(f"  {folder}: SCHEMA CHECK FAILED: {problems}", file=sys.stderr)
            continue
        write_json(GENERATED_TASKS_DIR / folder / "assurance_card.json", card)
        write_json(CARDS_DIR / f"{folder}.assurance_card.json", card)
        funnel_log(STAGE, task["candidate_id"], "accept",
                   f"schema-valid card written ({len(card['residual_risks'])} "
                   f"residual risk(s), "
                   f"{len(card['near_miss_battery']['residual_open_ids'])} open "
                   f"near-miss residual(s))")
        print(f"  {folder}: card written "
              f"(difficulty={card['difficulty']['structural_preliminary']}, "
              f"residual_risks={len(card['residual_risks'])}, "
              f"open_near_miss={card['near_miss_battery']['residual_open_ids']})")
        n_ok += 1

    print(f"wrote {n_ok}/{len(tasks)} cards to task folders and {CARDS_DIR}")
    return 0 if n_ok == len(tasks) else 1


if __name__ == "__main__":
    sys.exit(main())
