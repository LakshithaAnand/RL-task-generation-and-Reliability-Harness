# Stage Summary — Stages 9 & 10 (difficulty/diversity metadata, hardening registry, assurance cards, funnel report, one-command demo)

## What was built

### Stage 9 — `pipeline/metadata_tag.py`
Structural, preliminary difficulty per task from four deterministic signals,
each recorded next to the label so the tag is auditable:

| signal | rule | rationale |
|---|---|---|
| flipped_test_count | <=4 → 2 pts, 5–19 → 1, >=20 → 0 | fewer flipping tests → harder to localize |
| instruction_verbosity | symptom_only → 1, explicit → 0 | no file/function hint is harder |
| verifier_test_count | <=5 → 1, else 0 | narrow behavioral surface exposes the defect |
| call_graph_depth | >=2 → 1, else/unavailable → 0 | cheap AST BFS from public API functions to the mutated site's enclosing function; `None` (unreachable via simple Name/Attribute edges) is reported as unavailable and scores 0 |

Label: total <=1 easy, 2–3 medium, >=4 hard. Every label carries the verbatim
caveat **"empirical solve-rate anchoring is the designed next step, not yet
run."** — no empirical numbers are computed or implied anywhere.

Result on the 5 tasks: c02 easy, c03 hard, c05 easy, c06 medium, c07 medium.
Diversity tags (category / skill_type / difficulty) plus a coverage table:
4 of 6 (skill × difficulty) cells occupied, not clustered — and the honest
note that **all 5 tasks share one seed repo (tabulate)**; cross-repo
diversity is not yet demonstrated.

### Stage 10a — `pipeline/hardening_registry.json` + `pipeline/registry.py`
One entry per discovered verifier exploit. Currently one:
`spoof_reward_json` (discovered 2026-07-08; agent plants
`/logs/verifier/reward.json` and Harbor resolves json before txt), with the
scaffold change that closed it (hardened `test.sh`: delete planted reward
files, remove hostile conftest, pin `PYTHONPATH=/repo`), its guarding probe,
and its applicability precondition. **The shortcut battery asserts at startup
that every registry entry's guarding probe is live** (`integrity.py` calls
`assert_probes_live` before probing anything) — a defense whose probe
disappeared would fail the run loudly instead of reporting untested coverage.
The registry version is recorded on each task's battery results and card.

### Stage 10b — `pipeline/card_writer.py` + `schemas/assurance_card.schema.json`
One machine-readable Task Assurance Card per accepted task, aggregating all
evidence: provenance (repo, commit, template, seed, sha256s), failure
establishment, solvability rewards from the real harness, spec coverage +
alignment, verifier-synthesis admissions/discards with provenance labels
(`regression_copy` / `template_generated` / `llm_proposed`=0), shortcut
battery per-probe status **including the spoof_reward_json harden-loop
history** (`failed` → hardening applied → `blocked`, plus the post-harden
broken=0/oracle=1 re-confirmation) and registry version, the near-miss table
with **c07's `assert_true` residual explicitly OPEN**, the independent
handcheck note (c02 + c07 covered by `scripts/handcheck.sh`; other tasks
honestly marked not covered), structural difficulty with its caveat, an
explicit live-vs-precomputed evidence split, and a residual-risks list (never
empty — no task is risk-free). Cards are schema-checked and written to both
the task folder (outside `environment/`, invisible to the agent) and
`artifacts/cards/`.

### Stage 10c — `pipeline/funnel_report.py`
Human-readable funnel from `funnel.jsonl` with last-wins dedup by
(stage, item) so `make demo` re-runs don't double count. Separates the
task-level funnel (a reject removes a candidate) from sub-item audits
(per-edge-test admission, per-probe battery, per-near-miss verdicts).
Reports the acceptance rate and the top-rejecting gate **from the log,
not predicted**.

### Stage 10d — `make demo` (`scripts/demo.sh`)
One command re-runs the full pipeline from committed state: eligibility →
mutations → failure establishment → assembly → verifier synthesis →
alignment → solvability (real `harbor run`, oracle + nop) → shortcut battery
(with registry startup assert) → near-miss battery (committed patches
re-applied and re-scored) → registry check → difficulty/diversity → cards →
funnel report. Common model API keys are actively **unset** at the top of the
script, so zero-API-key operation is enforced, not just claimed. Per-stage
timing and a final summary are printed.

## Measured demo run (2026-07-08, Apple Silicon, warm Docker caches)

Total **6m55s (415s)**, exit 0, zero API keys. Per-stage: eligibility 3s,
mutations 0s, failure establishment 146s, assembly 1s, verifier synthesis
10s, alignment 1s, solvability (two real `harbor run` jobs) 89s, shortcut
battery 121s, near-miss battery 43s, registry check 0s,
difficulty+diversity 1s, cards 0s, funnel report 0s.

Everything reproduced live from committed state: the `spoof_reward_json`
probe **failed then was hardened then re-probed blocked on all 5 tasks**
(post-harden broken=0/oracle=1 re-confirmed each time); c03's `off_by_one`
near-miss slipped, was hardened with one admitted edge test, and re-ran
REJECT; c07's `assert_true` stayed OPEN (no distinguishing edge test).
Funnel: 8 candidates in → 5 accepted (62.5%); top-rejecting gate:
failure_establishment (3 rejects: one mutant hung the suite, two flipped no
test). Shortcut battery: 35/35 probes blocked. Near-miss battery: 14/15
rejected.

Operational notes: on this machine `/usr/bin/make` is gated behind an
unaccepted Xcode license, so the demo was run as `bash scripts/demo.sh`
(byte-identical to the `demo` target). One earlier attempt died in
eligibility when a `docker build` stalled on the registry for the full 900s
build timeout — transient; the rerun built from cache in 3s.

## Empirical difficulty run (2026-07-08, added after initial 9/10 completion)

`pipeline/empirical_difficulty.py` — a separate, model-dependent evaluation
step, deliberately NOT in demo.sh (the zero-key demo re-reads the committed
`artifacts/empirical_difficulty.json`; it never regenerates it). Agent
`claude-code` + `claude-haiku-4-5-20251001` (agent list and `-m` format
confirmed from `harbor run --help`), 5 attempts per task, 4 concurrent
trials on the local Docker provider. API key from env or a gitignored `.env`
(the runner refuses to read an unignored `.env`, and never prints the key).

Results (thresholds pre-committed in PLAN.md before any results:
easy ≥75%, medium 25–74%, hard <25%):

| task | structural | solve-rate | empirical | agree |
|---|---|---|---|---|
| c02-boundary_flip-L2446 | easy | 5/5 | easy | yes |
| c03-boundary_flip-L1458 | hard | 5/5 | easy | **no** |
| c05-inverted_condition-L121 | easy | 5/5 | easy | yes |
| c06-inverted_condition-L846 | medium | 5/5 | easy | **no** |
| c07-inverted_condition-L2393 | medium | 3/5 | medium | yes |

Disagreements recorded, not reconciled: the structural heuristic over-rated
c03 (few flipped tests ≠ hard for a capable agent — symptom text plus a
2-test failure signature localized it fine) and c06 (192 flipped tests make
the defect trivially loud, which the structural score partially credits but
under-weights). c07 is the only task that actually resisted: 3/5, with both
failures genuine wrong-fix attempts (8–9 min of agent effort, no timeouts),
consistent with its explicit-but-subtle inverted HTML-wrapping branch.

Surprises / friction / cost: zero timeouts and zero harness exceptions in
25 trials; wall clock 69–593s per trial; total agent-reported cost **$5.28**
(c07's two failures were the most expensive attempts, $0.70 and $1.04 — the
agent burns budget precisely where the task is hard). n=5 is small by
design: one success shifts a label; the cards carry that caveat verbatim.
The card's difficulty block now holds both labels plus an `agreement`
field, `empirical_solve_rate` is filled, and evidence_provenance marks
empirical results as precomputed (model-dependent), re-read live by demo.

## Checks proving it works
- `uv run pytest -q` — 35 passed, including: difficulty scoring thresholds
  and BFS depth, verbatim-caveat check, registry loads + every probe live +
  fails loudly on a dead probe, c07 card builds schema-valid with the
  residual explicit and the harden-loop history present, funnel dedup
  last-wins semantics.
- `make demo` — full end-to-end run, zero API keys.

## Caveats / honest notes
- Difficulty is structural only; thresholds are heuristic and fixed before
  any empirical anchoring. `call_graph_depth` is an approximation over
  intra-module Name/Attribute call edges; where the site is unreachable that
  is recorded and scored 0, not guessed.
- The coverage table shows spread across skill × difficulty, but all tasks
  come from one repo — stated on the report and on every card.
- The registry has a single entry because a single exploit has been
  discovered so far; the liveness assert is the mechanism that keeps future
  entries honest.
