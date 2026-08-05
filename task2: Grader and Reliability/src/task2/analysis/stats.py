"""Statistics helpers. Wilson interval is required; bootstrap is optional."""

from __future__ import annotations

import math
import random


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI for a binomial proportion. Behaves sanely at the
    small n this project honestly has (unlike the normal approximation)."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def bootstrap_interval(values: list[float], n_resamples: int = 2000,
                       alpha: float = 0.05, seed: int = 42) -> tuple[float, float]:
    """Percentile bootstrap CI for the mean of `values` (optional extra)."""
    if not values:
        return (0.0, 1.0)
    rng = random.Random(seed)
    means = sorted(
        sum(rng.choices(values, k=len(values))) / len(values)
        for _ in range(n_resamples)
    )
    lo = means[int((alpha / 2) * n_resamples)]
    hi = means[int((1 - alpha / 2) * n_resamples) - 1]
    return (lo, hi)


def fmt_ci(successes: int, n: int) -> str:
    lo, hi = wilson_interval(successes, n)
    p = successes / n if n else 0.0
    return f"{successes}/{n} = {p:.0%} (Wilson 95% CI: {lo:.0%}-{hi:.0%})"
