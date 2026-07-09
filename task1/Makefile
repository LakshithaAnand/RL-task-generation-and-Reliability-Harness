# Synthetic task generation pipeline — entry points.

.PHONY: demo test handcheck

# Re-run the FULL pipeline from committed state (zero API keys; Docker +
# harbor CLI required). Prints per-stage timing, the funnel report, and
# writes one assurance card per task.
demo:
	bash scripts/demo.sh

# The pipeline's own unit checks (never shipped into any task environment).
test:
	uv run pytest -q

# Standalone re-verification of the reward.json hardening + c07 residual.
handcheck:
	bash scripts/handcheck.sh
