"""Retrieval metrics and paired significance tests.

``score_query`` follows the Pass@k of the Anthropic contextual-embeddings
cookbook: for one query, the share of its golden chunks found in the top k, and
Pass@k is the mean over queries. ``failure`` is ``1 - Pass@k``, the quantity
the Contextual Retrieval post reports at k = 20.
"""

import math
import random
from collections.abc import Sequence


def score_query(retrieved: Sequence[str], golden: Sequence[str]) -> float:
    """Share of the golden chunk ids present in ``retrieved``."""
    if not golden:
        raise ValueError("a query needs at least one golden chunk")
    found = len(set(golden) & set(retrieved))
    return found / len(set(golden))


def score_alternatives(
    retrieved: Sequence[str], alternatives: Sequence[Sequence[str]]
) -> float:
    """Share of evidence items found, where each item lists the chunks that
    each satisfy it (a fact quoted in two overlapping chunks, for example)."""
    if not alternatives or any(not group for group in alternatives):
        raise ValueError("every evidence item needs at least one chunk")
    found = sum(1 for group in alternatives if set(group) & set(retrieved))
    return found / len(alternatives)


def reciprocal_rank(retrieved: Sequence[str], golden: Sequence[str]) -> float:
    """``1 / rank`` of the first golden chunk in ``retrieved``, 0 if absent."""
    wanted = set(golden)
    for rank, chunk_id in enumerate(retrieved, start=1):
        if chunk_id in wanted:
            return 1.0 / rank
    return 0.0


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def paired_bootstrap(
    baseline: Sequence[float],
    other: Sequence[float],
    resamples: int = 10_000,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Mean paired difference ``other - baseline`` with a 95% bootstrap CI.

    Queries are resampled with replacement, keeping each query's two scores
    together. Returns ``(mean_difference, low, high)``.
    """
    if len(baseline) != len(other) or not baseline:
        raise ValueError("paired samples must be non-empty and of equal length")
    diffs = [b - a for a, b in zip(baseline, other, strict=True)]
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(
        sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples)
    )
    return mean(diffs), means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]


def mcnemar(baseline: Sequence[bool], other: Sequence[bool]) -> tuple[int, int, float]:
    """Exact two-sided McNemar test on paired pass/fail outcomes.

    Returns ``(only_other, only_baseline, p_value)``: the counts of queries only
    the other condition passed and only the baseline passed, and the exact
    binomial p-value for the null that both are equally likely.
    """
    if len(baseline) != len(other):
        raise ValueError("paired samples must have equal length")
    gained = sum(1 for a, b in zip(baseline, other, strict=True) if b and not a)
    lost = sum(1 for a, b in zip(baseline, other, strict=True) if a and not b)
    discordant = gained + lost
    if discordant == 0:
        return gained, lost, 1.0
    tail = sum(math.comb(discordant, i) for i in range(min(gained, lost) + 1))
    return gained, lost, min(1.0, 2 * tail / 2**discordant)
