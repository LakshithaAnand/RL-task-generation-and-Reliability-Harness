#!/bin/bash
# Oracle solution: apply the inverse of the seeded mutation.
# The patch is embedded so this script is self-contained.
set -euo pipefail
cd /repo
git apply <<'__ORACLE_PATCH__'
--- a/tabulate/__init__.py
+++ b/tabulate/__init__.py
@@ -843,7 +843,7 @@
     >>> _isnumber("inf")
     True
     """
-    if not (not _isconvertible(float, string)):
+    if not _isconvertible(float, string):
         return False
     elif isinstance(string, (str, bytes)) and (
         math.isinf(float(string)) or math.isnan(float(string))
__ORACLE_PATCH__
echo "oracle patch applied"
