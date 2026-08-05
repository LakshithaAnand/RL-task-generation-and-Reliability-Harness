# Project: Synthetic Task Generation Pipeline (Terminal-Bench 2.0 / Harbor)

Full design: docs/PLAN.md — read it before any stage work.

## Thesis
The product is verifiable reward with an executable assurance case. Every accepted task ships evidence: starts unsolved, oracle-solvable, instruction–verifier traceable, rejects near-misses, documented residual risk.

## Hard rules
- Build ONE stage at a time. Never start the next stage unprompted.
- Every stage gets: implementation + a small runnable check proving it works.
- Deterministic checks are the only acceptance gates. LLM output never accepts/rejects a task.
- No LLM interprets diffs: specs come from mutation template metadata.
- Scoring tests live only in tests/, never in the agent's environment/.
- Every claim ships with its evidence and its caveat. No overclaiming in code comments, READMEs, or card fields.
- Prefer boring, readable Python. Small modules. No frameworks beyond necessity. Type hints. One clear entry point per stage.

## Conventions
- Python 3.11+, uv for deps, pytest for our own tests.
- Pipeline stages are modules under pipeline/, each with a CLI entry.
- All pipeline output artifacts are JSON; schemas in schemas/.
- Every stage writes rejects + reasons to the funnel log (funnel.jsonl).
- Git commit after each verified stage.

## Environment
- Docker Desktop must be running for anything touching Harbor.
- Harbor CLI installed per https://www.harborframework.com docs.
- Replay mode must run with ZERO API keys. Anything model-dependent is precomputed, committed, and re-executed deterministically.
- Host: macOS, Apple Silicon (arm64). Docker builds are linux/arm64 by default; flag any dependency that forces linux/amd64 emulation.