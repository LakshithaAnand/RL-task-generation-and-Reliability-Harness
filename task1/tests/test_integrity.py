"""Unit checks for Stage 8 pure logic: probe classification, near-miss materialization."""

from pipeline.integrity import _classify
from pipeline.nearmiss import materialize_patch

ORACLE_PATCH = """\
--- a/tabulate/__init__.py
+++ b/tabulate/__init__.py
@@ -1455,7 +1455,7 @@
             headers = list(map(str, range(len(rows[0]))))

     # take headers from the first row if necessary
-    if headers == "firstrow" and len(rows) >= 0:
+    if headers == "firstrow" and len(rows) > 0:
         if index is not None:
             headers = [index[0]] + list(rows[0])
"""


def test_classify_blocked_and_failed() -> None:
    assert _classify("RESOLVED_REWARD=0")[0] == "blocked"
    assert _classify("noise\nRESOLVED_REWARD=0.0\n")[0] == "blocked"
    assert _classify("RESOLVED_REWARD=1")[0] == "failed"
    assert _classify("RESOLVED_REWARD=1.0")[0] == "failed"
    assert _classify("garbage")[0] == "failed"     # no reward line => not blocked


def test_materialize_swaps_only_the_added_line() -> None:
    patch, broken_line, fix_line = materialize_patch(
        ORACLE_PATCH, '    if headers == "firstrow" and len(rows) > 1:')
    # the broken line (context/`-`) is preserved
    assert broken_line == '    if headers == "firstrow" and len(rows) >= 0:'
    assert fix_line == '    if headers == "firstrow" and len(rows) > 0:'
    # the near-miss replaced the `+` line
    assert '+    if headers == "firstrow" and len(rows) > 1:' in patch
    assert '+    if headers == "firstrow" and len(rows) > 0:' not in patch
    # headers and context untouched
    assert patch.startswith("--- a/tabulate/__init__.py\n+++ b/tabulate/__init__.py\n")
    assert '-    if headers == "firstrow" and len(rows) >= 0:' in patch


def test_materialized_patch_has_one_added_line() -> None:
    patch, _, _ = materialize_patch(ORACLE_PATCH, "    if X:")
    added = [l for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++")]
    assert added == ["+    if X:"]
