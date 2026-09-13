"""PCBF research-batch market-quality triage workflow.

See ``research_batch.py``'s own module docstring for the full pipeline
description, scope, and framing (market-quality triage, never outcome
selection or a recommendation of any kind).
"""

from .research_batch import main as screen_main, run_screen, screen_research_batch

__all__ = ["screen_main", "run_screen", "screen_research_batch"]
