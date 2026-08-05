"""Unit checks for pipeline.mutations: site discovery, reversibility, determinism."""

import ast

from pipeline.mutations import (
    apply_site,
    discover_sites,
    revert_site,
    unified_diff,
    validate_site,
)

FIXTURE = """\
def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x >= hi:
        return hi
    return x


def spin(n):
    total = 0
    while n > 0:
        total += n
        n -= 1
    return total
"""


def test_discovers_both_templates() -> None:
    sites = discover_sites(FIXTURE, "m.py")
    by_template = {t: [s for s in sites if s.template == t]
                   for t in ("boundary_flip", "inverted_condition")}
    # x < lo, x >= hi, n > 0 are comparisons; all three also appear as conditions
    assert len(by_template["boundary_flip"]) == 3
    assert len(by_template["inverted_condition"]) == 3
    ops = {s.original_text for s in by_template["boundary_flip"]}
    assert ops == {"<", ">=", ">"}


def test_boundary_flip_swaps_operator() -> None:
    site = next(s for s in discover_sites(FIXTURE, "m.py")
                if s.template == "boundary_flip" and s.original_text == ">=")
    mutated = apply_site(FIXTURE, site)
    assert "if x > hi:" in mutated
    assert "if x >= hi:" not in mutated


def test_inverted_condition_wraps_not() -> None:
    site = next(s for s in discover_sites(FIXTURE, "m.py")
                if s.template == "inverted_condition" and s.context_expr == "n > 0")
    mutated = apply_site(FIXTURE, site)
    assert "while not (n > 0):" in mutated


def test_round_trip_is_byte_exact_for_every_site() -> None:
    for site in discover_sites(FIXTURE, "m.py"):
        mutated = apply_site(FIXTURE, site)
        assert mutated != FIXTURE
        ast.parse(mutated)  # stays syntactically valid
        assert revert_site(mutated, site) == FIXTURE
        assert validate_site(FIXTURE, site) is None


def test_discovery_is_deterministic() -> None:
    a = discover_sites(FIXTURE, "m.py")
    b = discover_sites(FIXTURE, "m.py")
    assert [(s.template, s.abs_start, s.mutated_text) for s in a] == [
        (s.template, s.abs_start, s.mutated_text) for s in b
    ]


def test_unified_diff_headers() -> None:
    site = discover_sites(FIXTURE, "m.py")[0]
    patch = unified_diff(FIXTURE, apply_site(FIXTURE, site), "pkg/m.py")
    assert patch.startswith("--- a/pkg/m.py\n+++ b/pkg/m.py\n")


def test_tests_are_never_scanned_note() -> None:
    # Site discovery is per-source-text; test exclusion happens at file selection
    # (generate_candidates filters test_*.py and test dirs). This guard documents it.
    from pipeline.mutations import generate_candidates  # noqa: F401
