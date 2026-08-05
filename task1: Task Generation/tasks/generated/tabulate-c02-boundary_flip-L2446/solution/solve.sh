#!/bin/bash
# Oracle solution: apply the inverse of the seeded mutation.
# The patch is embedded so this script is self-contained.
set -euo pipefail
cd /repo
git apply <<'__ORACLE_PATCH__'
--- a/tabulate/__init__.py
+++ b/tabulate/__init__.py
@@ -2443,7 +2443,7 @@
 
         # Always ensure each line is color terminted if any colors are
         # still active, otherwise colors will bleed into other cells on the console
-        if len(self._active_codes) >= 0:
+        if len(self._active_codes) > 0:
             new_line = new_line + _ansi_color_reset_code
 
         lines.append(new_line)
__ORACLE_PATCH__
echo "oracle patch applied"
