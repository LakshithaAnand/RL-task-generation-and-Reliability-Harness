# Fix a defect in `tabulate`

The Python library `tabulate` (pretty-printing of tabular data) lives at `/repo` and is installed into the environment's Python in editable mode, so changes to `/repo` take effect immediately.

A logic bug was introduced in the `tabulate` library: one branch condition is inverted, so the code takes the wrong path in a specific situation. Find the inverted condition and correct it.

The repo's own developer tests under `/repo/test` currently pass and must still pass after your fix.

Done looks like:
- The defect is fixed at its source: a small change to the library code in `/repo`.
- The library's behavior matches its documented/expected semantics for the affected inputs.
- Correctness is judged by a held-out verifier that is not visible in this environment.
