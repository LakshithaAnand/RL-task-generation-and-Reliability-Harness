"""Unit checks for pipeline.common parsing helpers."""

from pipeline.common import parse_failed_tests, parse_pytest_summary

SAMPLE_GREEN = """\
........s..s
247 passed, 37 skipped in 1.17s
"""

SAMPLE_FAILED = """\
..F..F
=========================== short test summary info ===========================
FAILED test/test_output.py::test_plain - assert ...
FAILED test/test_output.py::test_grid[a] - assert ...
2 failed, 245 passed, 37 skipped in 1.31s
"""


def test_parse_summary_green() -> None:
    s = parse_pytest_summary(SAMPLE_GREEN)
    assert s["counts"] == {"passed": 247, "skipped": 37}
    assert s["duration_seconds"] == 1.17


def test_parse_summary_failed() -> None:
    s = parse_pytest_summary(SAMPLE_FAILED)
    assert s["counts"]["failed"] == 2
    assert s["counts"]["passed"] == 245


def test_parse_failed_tests() -> None:
    assert parse_failed_tests(SAMPLE_FAILED) == [
        "test/test_output.py::test_plain",
        "test/test_output.py::test_grid[a]",
    ]
    assert parse_failed_tests(SAMPLE_GREEN) == []
