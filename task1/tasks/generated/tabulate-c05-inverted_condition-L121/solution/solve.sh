#!/bin/bash
# Oracle solution: apply the inverse of the seeded mutation.
# The patch is embedded so this script is self-contained.
set -euo pipefail
cd /repo
git apply <<'__ORACLE_PATCH__'
--- a/tabulate/__init__.py
+++ b/tabulate/__init__.py
@@ -118,7 +118,7 @@
         return ("-" * (w - 1)) + ":"
     elif align == "center":
         return ":" + ("-" * (w - 2)) + ":"
-    elif not (align == "left"):
+    elif align == "left":
         return ":" + ("-" * (w - 1))
     else:
         return "-" * w
__ORACLE_PATCH__
echo "oracle patch applied"
