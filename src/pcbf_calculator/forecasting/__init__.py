"""Soccer 1X2 Poisson research forecast adapter and its walk-forward
evaluation tooling.

See ``soccer_1x2.py`` and ``evaluate.py``'s own module docstrings for the
full pipeline description, scope, and evidence contract.
"""

from .evaluate import main as evaluate_main, run_evaluation, run_evaluation_cli
from .soccer_1x2 import main as forecast_main, run_forecast, run_forecast_cli

__all__ = [
    "forecast_main",
    "run_forecast",
    "run_forecast_cli",
    "evaluate_main",
    "run_evaluation",
    "run_evaluation_cli",
]
