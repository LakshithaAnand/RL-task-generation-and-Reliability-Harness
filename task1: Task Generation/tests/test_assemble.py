"""Unit checks for pipeline.assemble: pruning, grouping, leak check, instruction."""

import ast

import pytest

from pipeline.assemble import (
    flipped_by_file,
    leak_check,
    prune_test_functions,
    render_instruction,
)

TEST_FILE = """\
import pytest
from common import helper  # noqa

def test_keep_me():
    assert helper() == 1

@pytest.mark.parametrize("x", [1, 2])
def test_flipped_param(x):
    assert x > 0

def test_flipped_plain():
    # comment inside
    assert True

def helper_not_a_test():
    return 42
"""


def test_flipped_by_file_strips_params() -> None:
    grouped = flipped_by_file([
        "test/test_a.py::test_flipped_param[1]",
        "test/test_a.py::test_flipped_param[2]",
        "test/test_a.py::test_flipped_plain",
        "test/test_b.py::test_other",
    ])
    assert grouped == {
        "test/test_a.py": {"test_flipped_param", "test_flipped_plain"},
        "test/test_b.py": {"test_other"},
    }


def test_prune_removes_functions_and_decorators() -> None:
    pruned = prune_test_functions(TEST_FILE, {"test_flipped_param", "test_flipped_plain"})
    assert "test_flipped_param" not in pruned
    assert "test_flipped_plain" not in pruned
    assert "parametrize" not in pruned          # decorator removed too
    assert "def test_keep_me" in pruned
    assert "def helper_not_a_test" in pruned
    ast.parse(pruned)


def test_prune_noop_when_no_match() -> None:
    assert prune_test_functions(TEST_FILE, {"test_absent"}) == TEST_FILE


META = {
    "repo": "tabulate",
    "file": "tabulate/__init__.py",
    "enclosing_function": "_format_table",
    "instruction_wording_template": (
        "A logic bug was introduced in the `{package}` library: one branch "
        "condition is inverted, so the code takes the wrong path in a "
        "specific situation. Find the inverted condition and correct it."
    ),
}


def test_instruction_verbosity_knob() -> None:
    explicit = render_instruction(META, "explicit")
    symptom = render_instruction(META, "symptom_only")
    assert "_format_table" in explicit
    assert "_format_table" not in symptom
    assert "tabulate/__init__.py" not in symptom
    for text in (explicit, symptom):
        assert "held-out verifier" in text


def test_leak_check_catches_test_names_and_paths() -> None:
    flipped = ["test/test_output.py::test_html[x]"]
    clean = render_instruction(META, "symptom_only")
    assert leak_check(clean, flipped) == []
    assert "test_html" in leak_check(clean + " see test_html", flipped)
    assert "test_output.py" in leak_check(clean + " test_output.py", flipped)
    assert "/tests/" in leak_check(clean + " look in /tests/", flipped)
