"""Bet9ja capture -> PCBF research-batch ingestion bridge.

Pure data plumbing: validate an assembled Bet9ja Soccer capture export
(``browser_extension/bet9ja_capture/soccer_session.js``'s own
``buildAssembledEnvelope`` output), admit only fully-supported pre-match
Soccer 1X2 fixtures, resolve each admitted fixture's kickoff time from the
page's own Africa/Lagos display text, and write a deterministic PCBF
research batch. No probability generation, no betting recommendation, no
staking, no ledger writes, no network calls -- see ``bet9ja.py``'s own
module docstring for the full in/out-of-scope list. Every fixture this
module ever emits carries ``classification_ceiling: "RESEARCH-MODEL"`` --
this bridge has no mechanism to authorize anything else (see
``docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md`` sections 14-17 for the real,
registry-gated promotion path RESEARCH-MODEL requires to ever change).
"""

from .bet9ja import ingest_assembled_capture, main as ingest_main

__all__ = ["ingest_assembled_capture", "ingest_main"]
