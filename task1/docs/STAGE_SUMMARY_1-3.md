# Stage Summary — Stages 1–3 (2026-07-08)

Compressed scope: ONE seed repo, 8 candidates, TWO mutation templates.
All gates are deterministic; no LLM is involved anywhere in Stages 1–3.

## Stage 1 — Seed eligibility (`pipeline/eligibility.py`)

Seed picked empirically among jsonschema/tabulate/humanize: **tabulate v0.9.0**
(`bf58e37e`) — smallest working tree (0.34 MB), MIT, single-module source
(2,716 lines: a dense mutation surface), test deps = pytest only. Fallbacks
(humanize 4.9.0, jsonschema v4.26.0) are pinned but commented out in
`pipeline/seeds.toml`. The kill rule was never triggered: the image built
first try (python:3.11-slim, editable install, `.git` kept for setuptools_scm).

Result: **ELIGIBLE** — clean build, suite green in-container
(**247 passed, 37 skipped in 1.17 s**, bound 120 s).

## Stage 2 — Mutation templates (`pipeline/mutations.py`)

Two AST-driven templates over source only (tests excluded):

| template | sites discovered | candidates written |
|---|---|---|
| boundary_flip (`>`↔`>=`, `<`↔`<=`) | 28 | 4 |
| inverted_condition (`test` → `not (test)`) | 159 | 4 |

Selection is seeded (RNG 42) over sites sorted by (file, line, col). Every
candidate is validated before writing: mutated source must parse, and
mutate→revert must reproduce the original **byte-for-byte**. Each
`artifacts/candidates/<id>/` ships `mutation.patch`, `oracle.patch` (the
inverse = known-good solution), and `metadata.json` (intended behavior,
changed condition, edge cases, instruction-safe wording template, source
hashes). 0 sampled sites were rejected by validation.

## Stage 3 — Failure establishment (`pipeline/failure_check.py`)

Baseline confirmed green once per run (fresh containers are identical, so a
per-candidate baseline would re-measure the same fact). Per candidate, one
container run: apply mutation → full suite → apply oracle → full suite.
Accept requires a clean pass→fail flip (pytest exit 1, ≥1 FAILED test) AND a
green oracle round-trip.

**Funnel: 8 candidates in → 5 survived (63%).**

| candidate | verdict | evidence |
|---|---|---|
| c01-boundary_flip-L2472 | REJECT | mutated suite **timed out** — the flip stalls `_handle_long_word` (no forward progress); a hang is not a clean flip |
| c02-boundary_flip-L2446 | ACCEPT | 18 tests flipped; round-trip green |
| c03-boundary_flip-L1458 | ACCEPT | 2 tests flipped; round-trip green |
| c04-boundary_flip-L2509 | REJECT | no test flipped (suite still green) |
| c05-inverted_condition-L121 | ACCEPT | 5 tests flipped; round-trip green |
| c06-inverted_condition-L846 | ACCEPT | 192 tests flipped; round-trip green |
| c07-inverted_condition-L2393 | ACCEPT | 4 tests flipped; round-trip green |
| c08-inverted_condition-L2139 | REJECT | no test flipped (suite still green) |

Top-rejecting reason this run: **no test flipped** (2 of 3 rejects) — the
seeded sampler landed in code the suite doesn't exercise. Flipped test IDs are
recorded in each candidate's `metadata.json` and in `funnel.jsonl`.

## Findings that changed the implementation

1. **Mutations can hang the suite, not just fail it.** c01's `<=`→`<` flip
   creates an infinite loop. Every in-container pytest run now has a hard
   `timeout` (120 s; baseline is ~1.2 s), classified as its own reject reason,
   with a host-side backstop that force-removes the container.
2. **A killed pytest corrupts line-based output parsing.** `timeout` kills
   pytest mid-progress-line, gluing the exit-code marker onto the dots line.
   Markers are now newline-prefixed and the parser is not line-anchored.

## Caveats (stated, not hidden)

- "Survived Stage 3" means *a test flipped and the oracle round-trips* — it
  says nothing yet about instruction quality, verifier strength, or shortcut
  resistance. Those are Stages 4–8 gates.
- c06 flips 192/247 tests — real, but likely too broad to make a good task;
  curation happens later, the funnel does not pre-judge it.
- Wall-clock per candidate is ~3 s (suite is fast); the 63% survival rate is
  one seed, one RNG draw — not a calibrated statistic.

## Wall-clock optimizations in effect

Docker image built once (Stage 1) and reused for every Stage-3 container;
tests only (no coverage/lint); one container per candidate covering both the
mutated run and the oracle round-trip.
