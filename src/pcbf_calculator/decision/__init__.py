"""PCBF decision layer (Release A, layer 3): a real rule engine over
explicit typed inputs. See ``docs/MULTI_SPORT_ARCHITECTURE.md``."""

from .engine import DecisionInputError, evaluate

__all__ = ["DecisionInputError", "evaluate"]
