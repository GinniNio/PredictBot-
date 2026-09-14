"""A real, OS-level exclusive lock shared by every ledger writer that needs
to make a preflight-then-commit sequence atomic across separate PROCESSES,
not merely within one call.

Extracted from ``pcbf_calculator.orchestration.forecast_ledger_writer``
(that module's own ``write_batch`` was the first caller, and its own
concurrency review found and fixed a genuine race -- see that module's
"Concurrency" docstring section for the full story) so a second writer
(``pcbf_calculator.orchestration.football_data_settlement``'s own
settlement batch) reuses the SAME lock primitive instead of a second,
independently-maintained copy of this logic.

Never touches ``ledgers/storage.py``'s lower-level primitives directly --
this only ever locks a dedicated ``<ledger_path>.lock`` file next to the
ledger, never the ledger file itself, so a plain reader
(``read_all``/``current_state``) is never blocked by a writer holding it.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import time
from pathlib import Path
from typing import Iterator

DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_POLL_INTERVAL_SECONDS = 0.1  # arbitrary short poll interval, unrelated to any backtest/promotion threshold


class LedgerLockTimeoutError(Exception):
    """Raised when the exclusive ledger lock cannot be acquired within
    ``timeout`` -- another process is currently writing to the same
    ledger. Nothing is written either way when this is raised."""


@contextlib.contextmanager
def exclusive_ledger_lock(ledger_path: Path, timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Held for a writer's entire preflight-then-commit sequence. A real,
    OS-level (POSIX advisory, ``fcntl.flock``) exclusive lock on a
    dedicated ``<ledger_path>.lock`` file next to the ledger. Polls rather
    than blocking indefinitely, so a writer that cannot acquire it within
    ``timeout`` raises ``LedgerLockTimeoutError`` (nothing written)
    instead of hanging forever behind a stuck or crashed prior writer."""

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LedgerLockTimeoutError(
                        f"Could not acquire the write lock for {ledger_path} within {timeout}s -- "
                        "another process appears to be writing to this ledger. Nothing was written."
                    )
                time.sleep(_LOCK_POLL_INTERVAL_SECONDS)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
