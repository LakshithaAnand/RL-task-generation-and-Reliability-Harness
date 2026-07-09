# Stage Summary — Stages 4 & 7 (2026-07-08)

Stages 5 (verifier synthesis), 6 (alignment), 8 (integrity audit) intentionally
not built yet; the current verifier is the flipped regression tests only.

## Stage 4 — Harbor assembly (`pipeline/assemble.py`)

All 5 Stage-3 survivors assembled into complete Harbor tasks under
`tasks/generated/` (org `tbgen` — Stage 0 finding: an invalid org/name makes
Harbor skip the task SILENTLY, so the name is regex-checked at assembly).

| task | verbosity | held-out tests |
|---|---|---|
| tbgen/tabulate-c02-boundary_flip-L2446 | explicit | 18 |
| tbgen/tabulate-c03-boundary_flip-L1458 | symptom_only | 2 |
| tbgen/tabulate-c05-inverted_condition-L121 | explicit | 5 |
| tbgen/tabulate-c06-inverted_condition-L846 | symptom_only | 192 |
| tbgen/tabulate-c07-inverted_condition-L2393 | explicit | 4 |

Per task: `instruction.md` generated procedurally from Stage-2 template
metadata (difficulty knob: `explicit` names the file/function, `symptom_only`
gives only template-level symptom wording; a deterministic leak check rejects
any instruction containing flipped-test names or verifier paths). The
environment builds FROM the cached Stage-1 seed image, applies the mutation at
build time, and ships the repo's ordinary dev tests with the flipped test
functions pruned out (line-span deletion, re-parsed). `tests/` holds the
held-out verifier (healthy-copy tests; runs only the flipped node IDs; writes
1/0 to `/logs/verifier/reward.txt`). `solution/solve.sh` embeds the oracle
patch as a heredoc. Spec JSON (requirement ↔ verifier-check mapping) written to
each candidate's artifacts folder.

**Leak closures applied at assembly** (verified by manual pre-flight of c03):
- `/repo/.git` is removed in the image — otherwise `git diff` reveals the
  exact mutation in one command.
- The mutation patch file is deleted in the same Docker layer.
- Flipped tests are absent from the visible dev suite (which passes on the
  broken repo — the defect is only observable via the instruction's symptom).

## Stage 7 — Solvability gate (`pipeline/solvability.py`)

Both directions through the **real harness** (`harbor run -p <parent>`,
absolute path, one job per agent; env images layer on the cached seed image):

| task | oracle | nop | verdict |
|---|---|---|---|
| tbgen/tabulate-c02-boundary_flip-L2446 | **1.0** | **0.0** | ACCEPT |
| tbgen/tabulate-c03-boundary_flip-L1458 | **1.0** | **0.0** | ACCEPT |
| tbgen/tabulate-c05-inverted_condition-L121 | **1.0** | **0.0** | ACCEPT |
| tbgen/tabulate-c06-inverted_condition-L846 | **1.0** | **0.0** | ACCEPT |
| tbgen/tabulate-c07-inverted_condition-L2393 | **1.0** | **0.0** | ACCEPT |

**5/5 passed both gates.** Rewards recorded in each candidate's
`metadata.json`; verdicts in `funnel.jsonl`; report in
`artifacts/solvability_report.json`.

## Deviations & findings

1. **`network_mode = "no-network"` is rejected by Harbor's local Docker
   provider** (no `disable_internet` capability), so tasks use `public`.
   Consequence: the "reinstall healthy package from PyPI" shortcut is NOT
   structurally closed — documented residual risk, to be probed by the
   Stage-8 shortcut battery.
2. **Harbor truncates trial names** in the job-level `result.json`
   (`...-L121` suffixes dropped), which broke reward attribution. Fixed by
   parsing each trial's own `result.json` (`task_name` +
   `verifier_result.rewards.reward`) — the authoritative source.
3. **One-off host anomaly, not reproduced:** during the first solvability run,
   `tabulate-c07.../environment/test/` vanished from the host between the
   oracle and nop jobs (nop env build failed on `COPY test/`). Harbor's compose
   templates bind-mount nothing from the task dir, and a full re-run with
   before/after directory checks did not reproduce it. Suspected filesystem
   sync hiccup (the repo lives in an iCloud-synced Documents folder). The
   assembler regenerates any task deterministically; per-trial parsing plus
   funnel reasons make such failures visible instead of silent.

## Caveats

- "Solvable + starts unsolved" is what Stage 7 proves — nothing more. The
  verifier is currently only the flipped regression tests; template-driven
  edge-case tests and near-miss hardening are Stage 5/8 work.
- The verbosity knob is assigned round-robin (3 explicit / 2 symptom_only),
  not calibrated to measured difficulty (Stage 9).
