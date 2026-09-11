"""Single-scalar temperature calibration for the Soccer 1X2 Elo baseline.

Fits one scalar `T > 0` applied to the model's PRE-SOFTMAX logits
(`softmax(logits / T)`) to minimize LOG LOSS (chosen because it is the
same metric the model itself was trained to minimize via cross-entropy —
using a different metric here would optimize calibration against a
criterion the model was never fit against) on `split_calibration_validation`
ONLY.

Determinism: a fixed, documented grid search over `[T_MIN, T_MAX]`
(`0.1` to `5.0`) with a fixed number of steps (`GRID_STEPS`, 491 points,
step size 0.01) — no external optimizer library, no randomness, and the
grid is walked in a fixed ascending order so ties always resolve to the
same (lowest) `T` deterministically (`min` with a stable key).

Structural calibration-data guarantee: `fit_temperature`'s signature names
its second argument `calibration_rows` specifically so `train.py`'s call
site is self-documenting about only ever passing
`split_calibration_validation`'s own rows — see
`tests/test_soccer_1x2_elo_baseline.py` for the test asserting the
returned `T` is identical whether or not `split_locked_test`/
`split_out_of_time_retrospective_holdout`/`split_genuine_prospective_scoring`
fixtures exist on disk at all.
"""

from __future__ import annotations

import math
from typing import Any

T_MIN = 0.1
T_MAX = 5.0
GRID_STEPS = 491  # step size (T_MAX - T_MIN) / (GRID_STEPS - 1) == 0.01

LOG_LOSS_PROBABILITY_FLOOR = 1e-12


def _log_loss_at_temperature(
    model: Any,
    calibration_rows: list[tuple[list[float], str]],
    temperature: float,
) -> float:
    total = 0.0
    for feature_vector, label in calibration_rows:
        probs = model.predict_proba(feature_vector, temperature=temperature)
        p = probs.get(label, 0.0)
        p = min(max(p, LOG_LOSS_PROBABILITY_FLOOR), 1.0 - LOG_LOSS_PROBABILITY_FLOOR)
        total += -math.log(p)
    return total / len(calibration_rows) if calibration_rows else 0.0


def fit_temperature(
    model: Any,
    calibration_rows: list[tuple[list[float], str]],
    t_min: float = T_MIN,
    t_max: float = T_MAX,
    grid_steps: int = GRID_STEPS,
) -> float:
    """`calibration_rows` is a list of `(feature_vector, actual_result)`
    pairs — this function has no knowledge of splits at all; the
    CALLER (`train.py`) is solely responsible for ensuring only
    `split_calibration_validation`'s rows are ever passed here.

    Returns the grid point in `[t_min, t_max]` minimizing mean log loss.
    Deterministic: the grid is a fixed, evenly-spaced ascending sequence,
    walked in order; `min` picks the FIRST (lowest-`T`) minimizer on a
    tie, so the result never depends on dict/set iteration order or
    floating-point-comparison happenstance beyond ordinary IEEE-754
    reproducibility."""

    if grid_steps < 2:
        raise ValueError("grid_steps must be >= 2")
    if not calibration_rows:
        return 1.0

    step = (t_max - t_min) / (grid_steps - 1)
    grid = [t_min + i * step for i in range(grid_steps)]

    best_t = grid[0]
    best_loss = _log_loss_at_temperature(model, calibration_rows, best_t)
    for t in grid[1:]:
        loss = _log_loss_at_temperature(model, calibration_rows, t)
        if loss < best_loss:
            best_loss = loss
            best_t = t
    return best_t
