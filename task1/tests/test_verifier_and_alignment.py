"""Unit checks for Stage 5 admission rule and Stage 6 alignment logic."""

from pipeline.alignment import check_alignment
from pipeline.verifier_synth import admit, render_edge_test_file

SPECS = [
    {"id": "a", "edge": "at_boundary", "code": "T([])"},
    {"id": "b", "edge": "above_boundary", "code": "T([[1]])"},
    {"id": "c", "edge": "at_boundary", "code": "T([[2]])"},
]


def test_admit_keeps_only_distinguishing() -> None:
    broken = {"a": {"ok": True, "val": "X"},      # differs -> admit
              "b": {"ok": True, "val": "same"},    # identical -> discard
              "c": {"ok": False, "err": "Boom"}}   # broken errors, oracle ok -> admit
    oracle = {"a": {"ok": True, "val": "Y"},
              "b": {"ok": True, "val": "same"},
              "c": {"ok": True, "val": "ok"}}
    admitted, discarded = admit(SPECS, broken, oracle)
    assert {s["id"] for s in admitted} == {"a", "c"}
    assert {d["id"] for d in discarded} == {"b"}
    assert admitted[0]["golden"] == "Y"


def test_admit_discards_when_oracle_errors() -> None:
    specs = [{"id": "a", "edge": "x", "code": "T([])"}]
    admitted, discarded = admit(
        specs, {"a": {"ok": False, "err": "e"}}, {"a": {"ok": False, "err": "e"}})
    assert admitted == []
    assert "oracle errored" in discarded[0]["reason"]


def test_render_edge_file_parses_and_uses_repr_golden() -> None:
    src = render_edge_test_file("boundary_flip",
                                [{"id": "a", "edge": "at_boundary",
                                  "code": "T([])", "golden": "line1\nline2"}])
    import ast
    ast.parse(src)                       # generated file is valid Python
    assert "'line1\\nline2'" in src      # golden embedded as a safe repr literal


def test_alignment_accepts_full_coverage() -> None:
    spec = {
        "requirements": [{"id": "R1"}],
        "verifier_checks": [
            {"id": "V1", "covers": ["R1"]},
            {"id": "V2", "covers": ["R1"]},
        ],
        "coverage": {"R1": ["V1", "V2"]},
    }
    assert check_alignment(spec)["aligned"] is True


def test_alignment_rejects_uncovered_requirement() -> None:
    spec = {
        "requirements": [{"id": "R1"}, {"id": "R2"}],
        "verifier_checks": [{"id": "V1", "covers": ["R1"]}],
        "coverage": {"R1": ["V1"], "R2": []},
    }
    r = check_alignment(spec)
    assert r["aligned"] is False
    assert r["uncovered_requirements"] == ["R2"]


def test_alignment_rejects_unstated_check() -> None:
    spec = {
        "requirements": [{"id": "R1"}],
        "verifier_checks": [
            {"id": "V1", "covers": ["R1"]},
            {"id": "V2", "covers": []},          # covers nothing -> unstated
        ],
        "coverage": {"R1": ["V1"]},
    }
    r = check_alignment(spec)
    assert r["aligned"] is False
    assert r["unstated_checks"] == ["V2"]


def test_alignment_rejects_check_referencing_missing_requirement() -> None:
    spec = {
        "requirements": [{"id": "R1"}],
        "verifier_checks": [{"id": "V1", "covers": ["R1", "R9"]}],
        "coverage": {"R1": ["V1"]},
    }
    assert check_alignment(spec)["aligned"] is False
