"""Pure-Python multinomial (3-class, softmax) logistic regression for the
Soccer 1X2 Elo baseline.

Stdlib only — no numpy/scipy/sklearn. Classes are Home/Draw/Away, in the
fixed, documented order `CLASS_ORDER = ("H", "D", "A")`.

Determinism (no randomness anywhere in fitting):
- Weights are zero-initialized.
- Training runs a FIXED number of iterations (`DEFAULT_ITERATIONS`, 2000,
  documented) of full-batch gradient descent — no shuffling (a full batch
  needs none), no `random` module usage anywhere in this file.
- A fixed learning rate (`DEFAULT_LEARNING_RATE`) is used throughout
  (no adaptive schedule that could depend on run-to-run floating-point
  nondeterminism beyond ordinary IEEE-754 determinism, which is itself
  fully reproducible given identical inputs and identical iteration
  order).
- L2 regularization at a fixed, documented default strength
  (`DEFAULT_L2_STRENGTH`, 1e-3) is added to the cross-entropy loss and its
  gradient; the bias/intercept term is never regularized (standard
  practice — only the true feature weights are penalized).

Standardization: the four Elo-derived numeric columns
(`home_elo_pre`, `away_elo_pre`, `elo_diff`, `season_stage` — everything in
`features.FEATURE_NAMES` except the constant `home_advantage` column and
the four already-binary `league_*` dummy columns) are z-scored using
TRAINING-SET-ONLY mean/std, computed once and stored as part of the fitted
model artifact (`standardization`), then reapplied IDENTICALLY at
evaluation time — this module never refits standardization parameters on
evaluation data. Standardizing keeps gradient descent converging in a
fixed, small iteration count regardless of Elo's raw ~1000-2000 scale.

Serialization: `ModelArtifact.to_dict()` / `from_dict()` round-trip a
plain-JSON-serializable dict (weight matrix as nested lists, standardization
params, feature names, league-category order, and self-describing
metadata) — this is written as JSON, NEVER pickle, so the artifact stays
host-neutral, inspectable, and dependency-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .features import DUMMY_LEAGUE_CODES, FEATURE_NAMES

CLASS_ORDER: tuple[str, ...] = ("H", "D", "A")

# Which FEATURE_NAMES columns get z-score standardized. `home_advantage`
# (always 1.0) and the four `league_*` dummies (already 0/1) are left
# untouched — standardizing a constant or an indicator column has no
# benefit and would divide-by-zero on a zero-variance column.
STANDARDIZED_FEATURE_NAMES: tuple[str, ...] = ("home_elo_pre", "away_elo_pre", "elo_diff", "season_stage")

DEFAULT_ITERATIONS = 2000
DEFAULT_LEARNING_RATE = 0.1
DEFAULT_L2_STRENGTH = 1e-3


@dataclass
class StandardizationParams:
    """Training-set-only mean/std per standardized feature column. `std`
    is floored at a tiny epsilon to avoid divide-by-zero on a
    (pathologically) zero-variance training column — this never happens
    with real Elo data but keeps this module crash-safe against a
    synthetic all-identical-rating fixture."""

    means: dict[str, float] = field(default_factory=dict)
    stds: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"means": dict(self.means), "stds": dict(self.stds)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StandardizationParams":
        return cls(means=dict(data["means"]), stds=dict(data["stds"]))


_STD_EPSILON = 1e-9


def fit_standardization(features: list[list[float]]) -> StandardizationParams:
    """Compute mean/std for each `STANDARDIZED_FEATURE_NAMES` column from
    `features` (rows in `FEATURE_NAMES` order) — TRAINING DATA ONLY, per
    this module's docstring."""

    means: dict[str, float] = {}
    stds: dict[str, float] = {}
    n = len(features)
    for name in STANDARDIZED_FEATURE_NAMES:
        col_index = FEATURE_NAMES.index(name)
        values = [row[col_index] for row in features]
        mean = sum(values) / n if n else 0.0
        variance = sum((v - mean) ** 2 for v in values) / n if n else 0.0
        std = math.sqrt(variance)
        means[name] = mean
        stds[name] = std if std > _STD_EPSILON else 1.0
    return StandardizationParams(means=means, stds=stds)


def apply_standardization(feature_vector: list[float], params: StandardizationParams) -> list[float]:
    """Apply already-fitted standardization to one row, in `FEATURE_NAMES`
    order. Never refits — `params` must come from `fit_standardization`
    called on training data only."""

    standardized = list(feature_vector)
    for name in STANDARDIZED_FEATURE_NAMES:
        col_index = FEATURE_NAMES.index(name)
        standardized[col_index] = (feature_vector[col_index] - params.means[name]) / params.stds[name]
    return standardized


def _softmax(logits: list[float]) -> list[float]:
    """Numerically-stable softmax (subtract max before exponentiating)."""

    max_logit = max(logits)
    exps = [math.exp(v - max_logit) for v in logits]
    total = sum(exps)
    return [v / total for v in exps]


@dataclass
class ModelArtifact:
    """Serializable fitted-model artifact. `weights[c]` is class `c`'s
    weight vector (`CLASS_ORDER` order), one entry per `FEATURE_NAMES`
    column; `biases[c]` is class `c`'s scalar bias/intercept."""

    feature_names: tuple[str, ...]
    class_order: tuple[str, ...]
    league_dummy_codes: tuple[str, ...]
    weights: list[list[float]]
    biases: list[float]
    standardization: StandardizationParams
    l2_strength: float
    iterations: int
    learning_rate: float
    final_loss: float
    training_row_count: int

    def logits(self, feature_vector: list[float]) -> list[float]:
        standardized = apply_standardization(feature_vector, self.standardization)
        return [
            self.biases[c] + sum(w * x for w, x in zip(self.weights[c], standardized))
            for c in range(len(self.class_order))
        ]

    def predict_proba(self, feature_vector: list[float], temperature: float = 1.0) -> dict[str, float]:
        """Raw (temperature=1.0) or temperature-scaled softmax
        probabilities, keyed by class label. Temperature scaling itself is
        `calibration.py`'s concern — this method just applies whatever `T`
        it is given to the logits before softmax."""

        raw_logits = self.logits(feature_vector)
        scaled = [v / temperature for v in raw_logits]
        probs = _softmax(scaled)
        return dict(zip(self.class_order, probs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_names": list(self.feature_names),
            "class_order": list(self.class_order),
            "league_dummy_codes": list(self.league_dummy_codes),
            "weights": [list(row) for row in self.weights],
            "biases": list(self.biases),
            "standardization": self.standardization.to_dict(),
            "l2_strength": self.l2_strength,
            "iterations": self.iterations,
            "learning_rate": self.learning_rate,
            "final_loss": self.final_loss,
            "training_row_count": self.training_row_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelArtifact":
        return cls(
            feature_names=tuple(data["feature_names"]),
            class_order=tuple(data["class_order"]),
            league_dummy_codes=tuple(data["league_dummy_codes"]),
            weights=[list(row) for row in data["weights"]],
            biases=list(data["biases"]),
            standardization=StandardizationParams.from_dict(data["standardization"]),
            l2_strength=data["l2_strength"],
            iterations=data["iterations"],
            learning_rate=data["learning_rate"],
            final_loss=data["final_loss"],
            training_row_count=data["training_row_count"],
        )


def _one_hot_label(label: str) -> list[float]:
    return [1.0 if label == c else 0.0 for c in CLASS_ORDER]


def _cross_entropy_loss(
    standardized_features: list[list[float]],
    one_hot_labels: list[list[float]],
    weights: list[list[float]],
    biases: list[float],
    l2_strength: float,
) -> float:
    n = len(standardized_features)
    total = 0.0
    for x, y in zip(standardized_features, one_hot_labels):
        logits = [biases[c] + sum(w * xi for w, xi in zip(weights[c], x)) for c in range(len(CLASS_ORDER))]
        probs = _softmax(logits)
        for c in range(len(CLASS_ORDER)):
            if y[c] > 0.0:
                p = max(probs[c], 1e-12)
                total += -math.log(p)
    l2_term = l2_strength * sum(w * w for row in weights for w in row)
    return total / n + l2_term


def fit(
    features: list[list[float]],
    labels: list[str],
    iterations: int = DEFAULT_ITERATIONS,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    l2_strength: float = DEFAULT_L2_STRENGTH,
    standardization: StandardizationParams | None = None,
) -> ModelArtifact:
    """Fit a 3-class multinomial logistic regression by full-batch
    gradient descent on the L2-regularized cross-entropy loss.
    Deterministic: zero-initialized weights, fixed iteration count, fixed
    learning rate, no shuffling, no `random` usage.

    `standardization`, when omitted, is fit HERE from `features` — the
    caller is expected to pass `features`/`labels` that are ALREADY
    training-split-only rows, so this default is correct for the normal
    "fit on split_training" call path. `train.py`'s own call site relies on
    exactly this default (it never passes `standardization` explicitly),
    so standardization ends up fit from `split_training`'s rows only,
    inside this function, on every real call path."""

    n = len(features)
    if n == 0:
        raise ValueError("fit() requires at least one training row")

    params = standardization if standardization is not None else fit_standardization(features)
    standardized_features = [apply_standardization(row, params) for row in features]
    one_hot_labels = [_one_hot_label(label) for label in labels]

    num_features = len(FEATURE_NAMES)
    num_classes = len(CLASS_ORDER)
    weights: list[list[float]] = [[0.0] * num_features for _ in range(num_classes)]
    biases: list[float] = [0.0] * num_classes

    for _ in range(iterations):
        # Full-batch gradient accumulation, always in the same fixed row
        # order (`standardized_features`/`one_hot_labels` are plain lists,
        # never a dict/set) — deterministic arithmetic order every run.
        grad_weights = [[0.0] * num_features for _ in range(num_classes)]
        grad_biases = [0.0] * num_classes

        for x, y in zip(standardized_features, one_hot_labels):
            logits = [biases[c] + sum(w * xi for w, xi in zip(weights[c], x)) for c in range(num_classes)]
            probs = _softmax(logits)
            for c in range(num_classes):
                error = probs[c] - y[c]
                grad_biases[c] += error
                for j in range(num_features):
                    grad_weights[c][j] += error * x[j]

        for c in range(num_classes):
            biases[c] -= learning_rate * (grad_biases[c] / n)
            for j in range(num_features):
                grad = grad_weights[c][j] / n + 2.0 * l2_strength * weights[c][j]
                weights[c][j] -= learning_rate * grad

    final_loss = _cross_entropy_loss(standardized_features, one_hot_labels, weights, biases, l2_strength)

    return ModelArtifact(
        feature_names=FEATURE_NAMES,
        class_order=CLASS_ORDER,
        league_dummy_codes=DUMMY_LEAGUE_CODES,
        weights=weights,
        biases=biases,
        standardization=params,
        l2_strength=l2_strength,
        iterations=iterations,
        learning_rate=learning_rate,
        final_loss=final_loss,
        training_row_count=n,
    )
