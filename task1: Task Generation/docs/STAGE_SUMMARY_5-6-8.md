# Stage Summary — Stages 5, 6, 8 (2026-07-08)

Stage 9 (difficulty/diversity) and Stage 10 (assurance card + funnel report) not
built yet. Everything below is deterministic; no LLM accepts or rejects anything.

## Stage 5 — Verifier synthesis (`pipeline/verifier_synth.py`)

Each task's held-out verifier = (a) confirmed regression copies of the flipped
tests + (b) template-driven edge-case characterization tests, admitted only if
they FAIL on broken and PASS on oracle in-container.

| task | template | regression | edge admitted / probed | admitted ids |
|---|---|---|---|---|
| c02 | boundary_flip | ✓ (18) | **4 / 12** | wrap_exact, wrap_words, wrap_longword_grid, wrap_width1 |
| c03 | boundary_flip | ✓ (2) | **0 / 12** | — |
| c05 | inverted_condition | ✓ (5) | **1 / 13** | pipe_left_right |
| c06 | inverted_condition | ✓ (13*) | **2 / 13** | pipe_center, unsafehtml |
| c07 | inverted_condition | ✓ (4) | **0 / 13** | — |

Total: **7 admitted, 56 discarded.** Every one of the 56 discards was
"broken output == oracle output (does not reveal the defect)" — the admission
rule refusing non-distinguishing tests, exactly as intended. c03/c07 admitted
zero: their defects are not reachable by the fixed public-API probe pool
(c03's bug shows only at the empty+firstrow corner; c07's is a return-**type**
difference invisible to string comparison — see the near-miss slip below). Those
tasks keep their regression verifier. Provenance (`regression_copy` vs
`template_generated`) is labeled per check in each task's spec JSON.

Disclosed caveat: edge tests are characterization tests whose golden value is
taken from the oracle — this proves *compatibility with the task*, not semantic
validity.

## Stage 6 — Alignment (`pipeline/alignment.py`)

Bidirectional requirement↔check traceability from spec JSON. **5/5 aligned**;
every requirement covered by ≥1 check, every check traces to a requirement,
coverage map consistent.

## Stage 8.1 — Shortcut battery (`pipeline/integrity.py`)

Seven deterministic probes per task; each runs in a fresh container (true reward
0), attempts a cheat, runs the real verifier, and is scored blocked / failed /
not_applicable. Reward resolution mirrors Harbor (reward.json wins over
reward.txt).

| probe | c02 | c03 | c05 | c06 | c07 |
|---|---|---|---|---|---|
| hardcode_reward_txt | blocked | blocked | blocked | blocked | blocked |
| spoof_reward_json | **blocked\*** | blocked\* | blocked\* | blocked\* | blocked\* |
| artifact_spoofing | blocked | blocked | blocked | blocked | blocked |
| exit_code_fake_pytest | blocked | blocked | blocked | blocked | blocked |
| env_poisoning_conftest | blocked | blocked | blocked | blocked | blocked |
| pip_reinstall | blocked | blocked | blocked | blocked | blocked |
| exposed_files | blocked | blocked | blocked | blocked | blocked |

**\* Finding + hardening (the headline of this stage).** `spoof_reward_json`
FAILED on all five tasks initially: an agent that writes
`/logs/verifier/reward.json = {"reward": 1}` wins, because Harbor resolves
`reward.json` ahead of the `reward.txt` our verifier wrote (confirmed in
Harbor's `verifier.py`). One hardening pass rewrote the verifier to (1) delete
any planted `reward.{txt,json}` before running, (2) remove a hostile
`/repo/conftest.py`, and (3) `PYTHONPATH=/repo` so `import tabulate` resolves to
the code under test rather than a pip-reinstalled copy. Re-probed → blocked, and
the broken=0 / oracle=1 property was re-confirmed on every task so the defense
cannot have silently broken solvability.

Two probes were correct by construction and validated our earlier scrubbing:
`pip_reinstall` (public network is available — the open risk noted in Stage 4 —
yet PYTHONPATH pinning blocks it) and `exposed_files` (no `.git`, patches,
held-out tests, or `/tests` in the agent environment). Note: `exposed_files`
first mis-fired on c02 as a *false positive* — a loose substring match hit a
sibling test (`test_wrap_text_to_colwidths_colors_wide_char`) sharing a prefix
with a held-out name; tightening the probe to match exact `def <name>(`
definitions confirmed the held-out tests really are pruned.

## Stage 8.2 — Near-miss battery (`pipeline/nearmiss.py`)

Three author-written almost-right patches per task (off-by-one, partial-fix /
no-op, assertion-gaming), materialized from each oracle patch, committed under
`artifacts/nearmiss/<task>/` labeled `llm_proposed`, each applied in a fresh
container and scored by the held-out verifier.

| task | n | rejected | rate | notes |
|---|---|---|---|---|
| c02 | 3 | 3 | 100% | |
| c03 | 3 | 3 | 100% | off_by_one initially SLIPPED, then hardened |
| c05 | 3 | 3 | 100% | |
| c06 | 3 | 3 | 100% | |
| c07 | 3 | 2 | 66% | **assert_true SLIPPED (residual)** |

**Overall rejection rate: 14/15 = 93%.** Two slips, both reported honestly:

- **c03 `off_by_one` (`len(rows) > 1`)** slipped the 2-test regression verifier
  (which never exercises a single-row `firstrow` table). One hardening pass
  admitted a distinguishing edge test (`T([["a","b"]], headers="firstrow")`);
  re-run → REJECT, broken=0/oracle=1 preserved. This required *relaxing* the
  edge-admission rule for hardening: a hardening test must pass-oracle and
  fail-near-miss but need not fail-broken, because a near-miss can differ from
  the oracle exactly where the broken state agrees with it.
- **c07 `assert_true` (`if True:`)** remains a slip and **cannot be hardened by
  this generator**. It differs from the oracle only in the *return type*
  (`JupyterHTMLStr` vs `str`) with identical string content, and c07's verifier
  covers only html cases. String-value characterization tests are blind to a
  type-only difference. Honest residual risk: catching it needs a type-aware
  assertion (an isinstance check), named as the extension. Recorded on the
  task's metadata for the assurance card.

## Findings that shaped the code

1. **reward.json > reward.txt in Harbor** — a real spoofing vector; verifier
   hardened to clear planted reward files.
2. **Loose substring leak-detection false-positives** — the exposed-files probe
   now matches exact test definitions.
3. **Hardening admission ≠ task admission** — hardening tests are exempted from
   fail-on-broken; the regression tests already cover the broken state.
4. **String-value characterization can't see return-type bugs** — the c07
   residual; documented, not hidden.
