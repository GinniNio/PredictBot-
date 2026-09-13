"""Inference-time scoring math for the Soccer 1X2 Elo v1 adapter.

**Why this exists instead of importing ``research.soccer_1x2_elo_baseline``
directly**: that package is a standalone research track, kept structurally
outside this repository's distributed package (``pyproject.toml`` only
packages ``src/`` -- ``research/`` is never part of an installed
``pcbf_football``/``pcbf_calculator`` wheel, and is not guaranteed
importable at runtime for any real host running this adapter). Training
code stays there, unduplicated, forever; this module is a small, faithful,
dependency-free copy of only the pure inference-time math a live adapter
actually needs to consume an already-trained artifact: the feature-vector
assembly (``features.py::build_feature_vector``) and the softmax
scoring (``model.py::ModelArtifact.predict_proba``).

**This is proven identical to the research module's own real functions,
not just similar** -- ``tests/test_soccer_1x2_elo_v1_adapter.py``'s
``ScoringFidelityTests`` imports both this module and
``research.soccer_1x2_elo_baseline``'s real ``model``/``features`` modules
side by side and asserts byte-for-byte identical output across a range of
real-shaped inputs. If the research module's math ever changes, that test
fails loudly rather than this adapter silently drifting from what its own
backtest evidence actually measured.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# Mirrors research/soccer_1x2_elo_baseline/features.py exactly -- see this
# module's own docstring for why it is a copy, not an import.
DUMMY_LEAGUE_CODES: tuple[str, ...] = ("D1", "SP1", "I1", "F1")
FEATURE_NAMES: tuple[str, ...] = (
    "home_elo_pre",
    "away_elo_pre",
    "elo_diff",
    "home_advantage",
    *[f"league_{code}" for code in DUMMY_LEAGUE_CODES],
    "season_stage",
)
STANDARDIZED_FEATURE_NAMES: tuple[str, ...] = ("home_elo_pre", "away_elo_pre", "elo_diff", "season_stage")


def league_one_hot(league_code: str) -> list[float]:
    return [1.0 if league_code == code else 0.0 for code in DUMMY_LEAGUE_CODES]


def build_feature_vector(home_elo_pre: float, away_elo_pre: float, league_code: str, season_stage: int) -> list[float]:
    return [
        home_elo_pre,
        away_elo_pre,
        home_elo_pre - away_elo_pre,
        1.0,
        *league_one_hot(league_code),
        float(season_stage),
    ]


@dataclass(frozen=True)
class StandardizationParams:
    means: dict[str, float]
    stds: dict[str, float]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StandardizationParams":
        return cls(means=dict(data["means"]), stds=dict(data["stds"]))


def apply_standardization(feature_vector: list[float], params: StandardizationParams) -> list[float]:
    standardized = list(feature_vector)
    for name in STANDARDIZED_FEATURE_NAMES:
        col_index = FEATURE_NAMES.index(name)
        standardized[col_index] = (feature_vector[col_index] - params.means[name]) / params.stds[name]
    return standardized


def _softmax(logits: list[float]) -> list[float]:
    max_logit = max(logits)
    exps = [math.exp(v - max_logit) for v in logits]
    total = sum(exps)
    return [v / total for v in exps]


@dataclass(frozen=True)
class ModelArtifact:
    class_order: tuple[str, ...]
    weights: list[list[float]]
    biases: list[float]
    standardization: StandardizationParams

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelArtifact":
        return cls(
            class_order=tuple(data["class_order"]),
            weights=[list(row) for row in data["weights"]],
            biases=list(data["biases"]),
            standardization=StandardizationParams.from_dict(data["standardization"]),
        )

    def logits(self, feature_vector: list[float]) -> list[float]:
        standardized = apply_standardization(feature_vector, self.standardization)
        return [
            self.biases[c] + sum(w * x for w, x in zip(self.weights[c], standardized))
            for c in range(len(self.class_order))
        ]

    def predict_proba(self, feature_vector: list[float], temperature: float = 1.0) -> dict[str, float]:
        raw_logits = self.logits(feature_vector)
        scaled = [v / temperature for v in raw_logits]
        probs = _softmax(scaled)
        return dict(zip(self.class_order, probs))
