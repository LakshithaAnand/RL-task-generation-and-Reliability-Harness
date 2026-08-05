#!/bin/bash
# Oracle solution: apply the inverse of the seeded mutation.
# The patch is embedded so this script is self-contained.
set -euo pipefail
cd /repo
git apply <<'__ORACLE_PATCH__'
--- a/tabulate/__init__.py
+++ b/tabulate/__init__.py
@@ -1455,7 +1455,7 @@
             headers = list(map(str, range(len(rows[0]))))
 
     # take headers from the first row if necessary
-    if headers == "firstrow" and len(rows) >= 0:
+    if headers == "firstrow" and len(rows) > 0:
         if index is not None:
             headers = [index[0]] + list(rows[0])
             index = index[1:]
__ORACLE_PATCH__
echo "oracle patch applied"
