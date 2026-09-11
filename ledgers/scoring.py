"""Forecast scoring: multiclass Brier score and log loss for one settled
forecast's H/D/A probabilities against the actual result.

Deliberately self-contained (not imported from
``research/soccer_1x2_elo_baseline/evaluate.py``): that module scores a
batch research backtest across many rows at once; this module scores one
live forecast the moment its real-world result arrives. The two are
different concerns that happen to share simple, standard formulas -- never
coupling this package to the research module's internals keeps each free
to change independently.

Never touches model weights, registration, admission, or thresholds --
this is read-only scoring of an already-recorded forecast against an
already-settled result.
"""

from __future__ import annotations

import math

LOG_LOSS_PROBABILITY_FLOOR = 1e-12

CLASS_ORDER = ("H", "D", "A")


def multiclass_brier(probabilities: dict[str, float], actual_outcome: str) -> float:
    """Sum of squared errors between each class's predicted probability
    and its 0/1 actual indicator, summed over H/D/A (the standard
    multiclass Brier score; 0.0 is a perfect forecast, 2.0 is the worst
    possible)."""

    return sum((probabilities.get(c, 0.0) - (1.0 if c == actual_outcome else 0.0)) ** 2 for c in CLASS_ORDER)


def log_loss(probabilities: dict[str, float], actual_outcome: str, floor: float = LOG_LOSS_PROBABILITY_FLOOR) -> float:
    """Negative log of the probability assigned to the actual outcome,
    floored away from 0/1 so a confident-but-wrong forecast never produces
    +/- infinity."""

    p = probabilities.get(actual_outcome, 0.0)
    p = min(max(p, floor), 1.0 - floor)
    return -math.log(p)
