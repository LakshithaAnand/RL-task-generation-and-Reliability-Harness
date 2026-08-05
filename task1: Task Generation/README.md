# tb-task-pipeline

Synthetic task generation for **Terminal-Bench 2.0 / Harbor**. The product is
*verifiable reward with an executable assurance case*: every accepted task
ships machine-readable evidence that it starts unsolved, is oracle-solvable,
is instruction–verifier traceable, rejects near-miss solutions, and carries
documented residual risk. Tasks are seeded by mutating a real repository, so
the oracle (the inverse of the mutation) and the failure witness (the tests
that flip) come for free — and every claim on a card was produced by an
executed, deterministic check, never an LLM verdict.

## Quickstart

```bash
bash scripts/demo.sh        # primary command — full pipeline, zero API keys
```

(`make demo` is an alias; on a Mac where the Xcode license was never
accepted, `/usr/bin/make` refuses to run — use the `bash` form.)

Requires Docker running, `uv`, and the Harbor CLI (`uv tool install harbor`).
First run downloads base images and clones the seed repo — about 7 minutes;
later runs hit the Docker cache. Model API keys are actively **unset** by the
script: everything model-generated (the near-miss patches) is committed and
only re-applied and re-scored, live.

## What the demo runs

1. **eligibility** — clone the pinned seed repo, license/size checks, green suite in Docker
2. **mutations** — seeded AST mutations (boundary_flip, inverted_condition); spec from template metadata, no LLM
3. **failure establishment** — ≥1 pre-existing test must flip pass→fail; oracle round-trip green
4. **harbor assembly** — standard task folders; scoring tests never enter the agent's environment
5. **verifier synthesis** — template-generated edge tests, admitted only if fail-broken AND pass-oracle
6. **alignment** — every requirement ↔ ≥1 verifier check, bidirectionally
7. **solvability** — real `harbor run`: oracle ⇒ 1.0, no-op ⇒ 0.0, both required
8. **integrity audit** — 7-probe shortcut battery (harden loop on failure) + near-miss battery (committed patches re-scored); registry startup assert
9. **difficulty + diversity** — structural label from recorded signals; coverage table
10. **assurance cards + funnel report** — the evidence, aggregated and printed

## Where to look

- **Showcase tasks** (rationale in [docs/CURATION.md](docs/CURATION.md)):
  - [tasks/generated/tabulate-c07-inverted_condition-L2393](tasks/generated/tabulate-c07-inverted_condition-L2393) — medium; harden loop on record **and** an honestly-open near-miss residual
  - [tasks/generated/tabulate-c03-boundary_flip-L1458](tasks/generated/tabulate-c03-boundary_flip-L1458) — hard; a near-miss slip closed by one admitted edge test
  - [tasks/generated/tabulate-c02-boundary_flip-L2446](tasks/generated/tabulate-c02-boundary_flip-L2446) — easy; strongest template-generated verifier (4 edge tests admitted)
- **Assurance cards**: `assurance_card.json` in each task folder, mirrored in [artifacts/cards/](artifacts/cards/)
- **Funnel report**: [artifacts/funnel_report.txt](artifacts/funnel_report.txt) — 8 candidates in → 5 accepted (62.5%); top-rejecting gate reported from the log, not predicted
- **Independent handcheck**: `bash scripts/handcheck.sh` — a standalone script (shares no pipeline code) that re-verifies the reward.json exploit is closed and that c07's residual is genuinely open

## Honest scope

Every card ends with a `residual_risks` list; read it before trusting a
reward. Set-level limits, stated up front:

- **One seed repo** (tabulate): the coverage table spans templates,
  verbosity, and difficulty, but cross-repo diversity is not yet demonstrated.
- **Empirical difficulty is n=5** — each task's label is anchored on 5
  attempts by one baseline agent (`claude-code` + `claude-haiku-4-5-20251001`)
  through the real harness, against thresholds pre-committed in
  [docs/PLAN.md](docs/PLAN.md). One success shifts a label: solve-rates are
  descriptive, not calibrated estimates. Structural labels are kept alongside;
  disagreements are recorded on the cards, not reconciled. Regenerating these
  runs (`uv run python -m pipeline.empirical_difficulty`) requires an
  Anthropic API key via environment variable or a **gitignored** `.env` —
  the zero-key demo never needs it; it re-reads the committed
  `artifacts/empirical_difficulty.json`.
- **c07 has an open residual**: an assertion-gaming near-miss passes its
  verifier; no distinguishing edge test was found. It is on the card, not
  hidden.
- **network_mode is "public"** (Harbor's local Docker provider rejects
  "no-network"): the pip-reinstall shortcut is probed and blocked; other
  network routes are unprobed.

## Layout

- `pipeline/` — one module + CLI per stage; `hardening_registry.json` (exploit ledger, probe-liveness asserted at battery startup)
- `tasks/generated/` — the 5 validated Harbor tasks (3 showcase + 2 additional), each with its assurance card
- `artifacts/` — all stage reports, funnel log + report, cards, near-miss patches (committed), candidates
- `schemas/` — assurance-card JSON schema
- `scripts/` — `demo.sh` (full pipeline), `handcheck.sh` (independent re-verification), `check_env.sh` (toolchain)
- `tests/` — the pipeline's own pytest checks (`make test`); never shipped into any task environment
- `docs/` — [PLAN.md](docs/PLAN.md) (design), [CURATION.md](docs/CURATION.md), stage summaries
