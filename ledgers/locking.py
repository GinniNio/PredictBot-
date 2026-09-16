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

**Platform backend.** ``fcntl.flock`` does not exist on Windows -- a
module-level ``import fcntl`` unconditionally raised ``ImportError`` the
moment ANY caller of this module (every settlement/writer command in this
package) was imported, before a single line of that command's own logic
ever ran, on a Windows installation of this project's wheel. The lock
primitive is therefore platform-dispatched at import time: POSIX
(``sys.platform != "win32"``) keeps ``fcntl.flock`` on the exact same
dedicated lock file, byte-for-byte the same behavior as before; Windows
uses ``msvcrt.locking`` against that same dedicated lock file instead.
Both backends implement the identical ``_try_lock(fd) -> bool`` /
``_unlock(fd) -> None`` contract below, so ``exclusive_ledger_lock`` itself
-- its public signature, its timeout/retry/poll loop, its
try/finally cleanup, and every existing caller -- is completely
unchanged; only which OS primitive actually backs the lock differs.

``msvcrt.locking`` locks a byte RANGE starting at the file's current
position, not the whole file by name -- this module guarantees the lock
file is never empty (writing one placeholder byte the first time it's
created) and always seeks to offset 0 before every lock/unlock attempt,
so the SAME single-byte region is always the one being contended for.
Windows byte-range locks are scoped per open file HANDLE (not per
process), so two independent ``os.open()`` calls -- whether from two
threads in the same process or two genuinely separate processes --
correctly exclude each other, exactly like ``fcntl.flock``'s own
per-open-file-description semantics on POSIX.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path
from typing import Iterator

DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_POLL_INTERVAL_SECONDS = 0.1  # arbitrary short poll interval, unrelated to any backtest/promotion threshold

# The single byte range every lock/unlock call below always targets --
# see this module's own docstring for why msvcrt needs this at all
# (fcntl.flock has no such requirement, but locking the same fixed range
# on both backends keeps exclusive_ledger_lock's own logic identical
# regardless of platform).
_LOCK_REGION_BYTES = 1

if sys.platform == "win32":
    import msvcrt

    def _ensure_lock_file_nonempty(fd: int) -> None:
        # msvcrt.locking locks a byte RANGE of the file -- locking a
        # region that doesn't exist yet (a freshly created, empty lock
        # file) is not reliably supported, so this guarantees at least
        # _LOCK_REGION_BYTES exists before the first lock attempt. A
        # lock file that already has content (a previous run, or another
        # process that created it first) is left exactly as-is.
        if os.fstat(fd).st_size < _LOCK_REGION_BYTES:
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, b"\0" * _LOCK_REGION_BYTES)

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, _LOCK_REGION_BYTES)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, _LOCK_REGION_BYTES)

else:
    import fcntl

    def _ensure_lock_file_nonempty(fd: int) -> None:
        # fcntl.flock locks the whole open file description, never a byte
        # range -- nothing to ensure here. Kept as a no-op (rather than
        # omitted) so exclusive_ledger_lock's own call site never needs an
        # `if sys.platform == "win32"` branch of its own.
        return

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class LedgerLockTimeoutError(Exception):
    """Raised when the exclusive ledger lock cannot be acquired within
    ``timeout`` -- another process is currently writing to the same
    ledger. Nothing is written either way when this is raised."""


@contextlib.contextmanager
def exclusive_ledger_lock(ledger_path: Path, timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Held for a writer's entire preflight-then-commit sequence. A real,
    OS-level exclusive lock (POSIX advisory ``fcntl.flock``, or Windows
    ``msvcrt.locking`` -- see this module's own docstring for the
    platform dispatch) on a dedicated ``<ledger_path>.lock`` file next to
    the ledger. Polls rather than blocking indefinitely, so a writer that
    cannot acquire it within ``timeout`` raises ``LedgerLockTimeoutError``
    (nothing written) instead of hanging forever behind a stuck or
    crashed prior writer."""

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    # Tracks whether THIS fd actually holds the lock -- fcntl.flock(LOCK_UN)
    # on an fd that never held a lock is a harmless no-op on POSIX, but
    # msvcrt.locking(LK_UNLCK, ...) on a region this handle never locked
    # raises PermissionError on Windows (confirmed via real Windows CI: a
    # timed-out acquire attempt -- never having held the lock at all --
    # unconditionally calling _unlock in the old code raised there, which
    # then skipped the os.close(fd) right after it in the same finally
    # block, leaking the handle -- and a still-open handle is exactly what
    # made Windows refuse to delete the lock file afterward, "used by
    # another process," even though that "other process" was this same
    # one). Only ever call _unlock when _try_lock actually returned True
    # for this fd, on every platform.
    locked = False
    try:
        _ensure_lock_file_nonempty(fd)
        deadline = time.monotonic() + timeout
        while True:
            if _try_lock(fd):
                locked = True
                break
            if time.monotonic() >= deadline:
                raise LedgerLockTimeoutError(
                    f"Could not acquire the write lock for {ledger_path} within {timeout}s -- "
                    "another process appears to be writing to this ledger. Nothing was written."
                )
            time.sleep(_LOCK_POLL_INTERVAL_SECONDS)
        yield
    finally:
        try:
            if locked:
                _unlock(fd)
        finally:
            # Always closed, even if _unlock itself unexpectedly raises --
            # a leaked, still-open handle is precisely what breaks a
            # caller's own later cleanup (e.g. shutil.rmtree) on Windows.
            os.close(fd)
