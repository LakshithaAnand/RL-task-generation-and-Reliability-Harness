"""Runnable checks for Stage 9 (metadata_tag) and Stage 10 (registry, cards,
funnel report). These run against committed artifacts where possible and pure
functions everywhere else — no Docker, no network."""

import json

import pytest

from pipeline import card_writer, empirical_difficulty, funnel_report, metadata_tag, registry
from pipeline.common import ASSEMBLY_REPORT_PATH
from pipeline.integrity import PROBES


# --- Stage 9: difficulty scoring ------------------------------------------

def test_difficulty_points_and_labels():
    assert metadata_tag._points_flipped(2) == 2
    assert metadata_tag._points_flipped(4) == 2
    assert metadata_tag._points_flipped(5) == 1
    assert metadata_tag._points_flipped(19) == 1
    assert metadata_tag._points_flipped(20) == 0
    assert metadata_tag._points_verbosity("symptom_only") == 1
    assert metadata_tag._points_verbosity("explicit") == 0
    assert metadata_tag._points_verifier(5) == 1
    assert metadata_tag._points_verifier(6) == 0
    assert metadata_tag._points_depth(None) == 0
    assert metadata_tag._points_depth(1) == 0
    assert metadata_tag._points_depth(2) == 1
    assert metadata_tag._label(1) == "easy"
    assert metadata_tag._label(2) == "medium"
    assert metadata_tag._label(3) == "medium"
    assert metadata_tag._label(4) == "hard"


def test_call_graph_depth_bfs():
    src = (
        "def public(x):\n    return _a(x)\n\n"
        "def _a(x):\n    return _b(x)\n\n"
        "def _b(x):\n    return x\n\n"
        "def _island(x):\n    return x\n"
    )
    assert metadata_tag.call_graph_depth(src, "public") == 0
    assert metadata_tag.call_graph_depth(src, "_a") == 1
    assert metadata_tag.call_graph_depth(src, "_b") == 2
    assert metadata_tag.call_graph_depth(src, "_island") is None
    assert metadata_tag.call_graph_depth(src, "missing") is None


def test_difficulty_caveat_is_verbatim():
    assert metadata_tag.DIFFICULTY_CAVEAT == (
        "empirical solve-rate anchoring is the designed next step, not yet run."
    )


def test_coverage_table_single_repo_note():
    records = [
        {"difficulty": {"structural_preliminary": "medium"},
         "diversity": {"skill_type": "control-flow-reasoning", "repo": "tabulate"}},
        {"difficulty": {"structural_preliminary": "hard"},
         "diversity": {"skill_type": "boundary-condition-reasoning", "repo": "tabulate"}},
    ]
    cov = metadata_tag.coverage_table(records)
    assert cov["repos"] == {"tabulate": 2}
    assert cov["single_repo_note"] is not None
    assert cov["clustered_in_one_cell"] is False


# --- Stage 10a: hardening registry ------------------------------------------

def test_registry_loads_and_every_probe_is_live():
    reg = registry.load_registry()
    assert reg["version"] >= 1
    assert reg["entries"], "registry must have at least the spoof_reward_json entry"
    registry.assert_probes_live(reg, set(PROBES))


def test_registry_fails_loudly_on_dead_probe():
    reg = registry.load_registry()
    reg["entries"].append({
        "id": "ghost", "discovered": "2026-07-08", "description": "x",
        "scaffold_change": "x", "guarding_probe": "no_such_probe",
        "applicability_precondition": "x",
    })
    with pytest.raises(registry.RegistryError, match="no_such_probe"):
        registry.assert_probes_live(reg, set(PROBES))


# --- Stage 10b: assurance cards ----------------------------------------------

def _c07_task():
    assembly = json.loads(ASSEMBLY_REPORT_PATH.read_text())
    return next(t for t in assembly["tasks"]
                if t["candidate_id"].startswith("c07"))


def test_c07_card_builds_and_validates():
    # requires Stage 9 to have tagged metadata at least once (committed state)
    meta = json.loads(
        (card_writer.CANDIDATES_DIR / _c07_task()["candidate_id"]
         / "metadata.json").read_text())
    if "difficulty" not in meta:
        pytest.skip("stage 9 not yet run on committed artifacts")
    card = card_writer.build_card(_c07_task(), registry.load_registry())
    schema = json.loads(card_writer.SCHEMA_PATH.read_text())
    assert card_writer.validate_against_schema(card, schema) == []
    # the honest residual must be explicit on the card
    assert card["near_miss_battery"]["residual_open_ids"] == ["assert_true"]
    assert any("OPEN RESIDUAL" in r and "assert_true" in r
               for r in card["residual_risks"])
    # harden-loop history must be present on the guarded probe
    spoof = card["shortcut_battery"]["probes"]["spoof_reward_json"]
    assert spoof["harden_loop"]["status_before_hardening"] == "failed"
    assert spoof["harden_loop"]["registry_entry"] == "spoof_reward_json"
    assert card["shortcut_battery"]["registry_version"] >= 1
    # difficulty caveat/empirical fields depend on whether the model-dependent
    # empirical run has been recorded (committed artifact); both states valid
    if card_writer.EMPIRICAL_PATH.exists():
        emp = json.loads(card_writer.EMPIRICAL_PATH.read_text())
        assert card["difficulty"]["caveat"] == card_writer._empirical_caveat(
            emp["attempts_per_task"])
        assert card["difficulty"]["empirical_solve_rate"] is not None
        assert card["difficulty"]["empirical"]["label"] in ("easy", "medium", "hard")
        assert card["difficulty"]["agreement"]["note"] == \
            "disagreements recorded, not reconciled"
        assert len(card["difficulty"]["empirical"]["per_attempt_rewards"]) == \
            emp["attempts_per_task"]
        assert any("empirical difficulty solve-rates" in p for p in
                   card["evidence_provenance"]["precomputed_or_authored"])
    else:
        assert card["difficulty"]["caveat"] == metadata_tag.DIFFICULTY_CAVEAT
        assert card["difficulty"]["empirical_solve_rate"] is None
    assert card["independent_handcheck"]["covered"] is True


def test_empirical_label_thresholds_precommitted():
    # thresholds from docs/PLAN.md: easy >=75%, medium 25-74%, hard <25%
    assert empirical_difficulty.empirical_label(1.0) == "easy"
    assert empirical_difficulty.empirical_label(0.75) == "easy"
    assert empirical_difficulty.empirical_label(0.74) == "medium"
    assert empirical_difficulty.empirical_label(0.4) == "medium"
    assert empirical_difficulty.empirical_label(0.25) == "medium"
    assert empirical_difficulty.empirical_label(0.2) == "hard"
    assert empirical_difficulty.empirical_label(0.0) == "hard"


def test_schema_check_catches_missing_sections():
    schema = json.loads(card_writer.SCHEMA_PATH.read_text())
    problems = card_writer.validate_against_schema({"card_version": 1}, schema)
    assert any("provenance" in p for p in problems)
    assert any("residual_risks" in p for p in problems)


# --- Stage 10c: funnel report ---------------------------------------------------

def test_funnel_dedupe_last_wins(tmp_path):
    log = tmp_path / "funnel.jsonl"
    lines = [
        {"ts": "t1", "stage": "mutation", "item": "c01-x", "verdict": "accept", "reason": "a"},
        {"ts": "t2", "stage": "failure_establishment", "item": "c01-x", "verdict": "reject", "reason": "no flip"},
        # re-run flips the same item to accept: last wins
        {"ts": "t3", "stage": "failure_establishment", "item": "c01-x", "verdict": "accept", "reason": "2 flipped"},
        {"ts": "t4", "stage": "integrity_shortcut", "item": "c01-x:probe", "verdict": "accept", "reason": "blocked"},
    ]
    log.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    entries, n_raw = funnel_report.load_deduped(log)
    assert n_raw == 4
    assert len(entries) == 3
    summary = funnel_report.summarize(entries)
    assert summary["candidates_in"] == 1
    assert summary["candidates_accepted"] == 1
    fe = summary["per_stage"]["failure_establishment"]["task_level"]
    assert (fe["accept"], fe["reject"]) == (1, 0)
    sub = summary["per_stage"]["integrity_shortcut"]["sub_item"]
    assert sub["accept"] == 1


def test_funnel_report_on_committed_log():
    entries, n_raw = funnel_report.load_deduped()
    summary = funnel_report.summarize(entries)
    assert summary["candidates_in"] >= 8
    assert summary["top_rejecting_gate"] is not None
    text = funnel_report.render(summary, n_raw, len(entries))
    assert "acceptance rate" in text
    assert "reported, not predicted" in text
