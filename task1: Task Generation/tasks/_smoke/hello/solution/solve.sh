#!/bin/bash
# Oracle solution for _smoke/hello.
# Copied to /solution by the oracle agent and run from the working directory (/app).
set -euo pipefail

printf 'hello world' > /app/hello.txt
