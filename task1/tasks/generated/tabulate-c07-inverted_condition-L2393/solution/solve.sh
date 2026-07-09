#!/bin/bash
# Oracle solution: apply the inverse of the seeded mutation.
# The patch is embedded so this script is self-contained.
set -euo pipefail
cd /repo
git apply <<'__ORACLE_PATCH__'
--- a/tabulate/__init__.py
+++ b/tabulate/__init__.py
@@ -2390,7 +2390,7 @@
 
     if headers or rows:
         output = "\n".join(lines)
-        if not (fmt.lineabove == _html_begin_table_without_header):
+        if fmt.lineabove == _html_begin_table_without_header:
             return JupyterHTMLStr(output)
         else:
             return output
__ORACLE_PATCH__
echo "oracle patch applied"
