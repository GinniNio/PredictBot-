"""Tests for ``ledgers/locking.py``'s ``exclusive_ledger_lock`` --
platform-dispatched (POSIX ``fcntl.flock`` / Windows ``msvcrt.locking``),
but this test module itself is entirely platform-agnostic: it exercises
``exclusive_ledger_lock``'s own public contract only, so running it on a
``windows-latest`` CI runner is what actually proves the ``msvcrt``
backend works for real, with zero test-code changes needed per platform.

Style matches ``tests/test_forecast_ledger_writer.py``'s own
``ConcurrencyTests`` -- plain ``unittest.TestCase``, real threads to
force a genuine race deterministically."""

from __future__ import annotations

import multiprocessing
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers.locking import DEFAULT_LOCK_TIMEOUT_SECONDS, LedgerLockTimeoutError, exclusive_ledger_lock  # noqa: E402


class BasicAcquireReleaseTests(unittest.TestCase):
    def test_the_lock_can_be_acquired_and_the_critical_section_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            ran = []
            with exclusive_ledger_lock(ledger_path, timeout=5):
                ran.append(True)
            self.assertEqual(ran, [True])

    def test_a_dedicated_lock_file_is_created_next_to_the_ledger_never_the_ledger_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            with exclusive_ledger_lock(ledger_path, timeout=5):
                pass
            lock_path = ledger_path.with_name(ledger_path.name + ".lock")
            self.assertTrue(lock_path.exists())
            self.assertFalse(ledger_path.exists())  # this module never creates the ledger file itself

    def test_the_lock_is_released_after_successful_use_a_second_acquire_succeeds_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            with exclusive_ledger_lock(ledger_path, timeout=5):
                pass
            started = time.monotonic()
            with exclusive_ledger_lock(ledger_path, timeout=5):
                pass
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 1.0, "second acquire should not have needed to wait at all")

    def test_the_lock_is_released_after_an_exception_inside_the_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            with self.assertRaises(ValueError):
                with exclusive_ledger_lock(ledger_path, timeout=5):
                    raise ValueError("boom -- simulated failure mid-critical-section")

            # The lock must be released even though the prior context
            # exited via an exception, never left held indefinitely.
            started = time.monotonic()
            with exclusive_ledger_lock(ledger_path, timeout=5):
                pass
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 1.0, "acquire after an exception should not have needed to wait")

    def test_reacquiring_many_times_in_sequence_never_leaks_a_held_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            for _ in range(5):
                started = time.monotonic()
                with exclusive_ledger_lock(ledger_path, timeout=5):
                    pass
                self.assertLess(time.monotonic() - started, 1.0)


class TimeoutTests(unittest.TestCase):
    def test_a_second_holder_times_out_while_the_first_still_holds_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            release_first = threading.Event()
            first_acquired = threading.Event()

            def hold_first():
                with exclusive_ledger_lock(ledger_path, timeout=5):
                    first_acquired.set()
                    release_first.wait(timeout=5)

            t = threading.Thread(target=hold_first)
            t.start()
            try:
                self.assertTrue(first_acquired.wait(timeout=5), "first holder never acquired the lock")
                started = time.monotonic()
                with self.assertRaises(LedgerLockTimeoutError):
                    with exclusive_ledger_lock(ledger_path, timeout=0.5):
                        pass  # pragma: no cover -- must never actually enter
                elapsed = time.monotonic() - started
                self.assertGreaterEqual(elapsed, 0.5)
                self.assertLess(elapsed, 3.0, "timeout took far longer than the requested 0.5s")
            finally:
                release_first.set()
                t.join(timeout=5)

    def test_timeout_error_message_names_the_ledger_path_never_the_lock_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            release_first = threading.Event()
            first_acquired = threading.Event()

            def hold_first():
                with exclusive_ledger_lock(ledger_path, timeout=5):
                    first_acquired.set()
                    release_first.wait(timeout=5)

            t = threading.Thread(target=hold_first)
            t.start()
            try:
                self.assertTrue(first_acquired.wait(timeout=5))
                with self.assertRaises(LedgerLockTimeoutError) as ctx:
                    with exclusive_ledger_lock(ledger_path, timeout=0.2):
                        pass  # pragma: no cover
                self.assertIn(str(ledger_path), str(ctx.exception))
            finally:
                release_first.set()
                t.join(timeout=5)

    def test_default_timeout_constant_is_used_when_none_is_supplied(self):
        # Not exercised end-to-end (that would need a real 30s wait) --
        # just confirms the public default hasn't silently changed.
        self.assertEqual(DEFAULT_LOCK_TIMEOUT_SECONDS, 30.0)


class TwoThreadsCompetingTests(unittest.TestCase):
    """Two simultaneous holders (same process, separate threads -- each
    exclusive_ledger_lock call opens its OWN file descriptor/handle, so
    this genuinely exercises the OS-level exclusion, not merely a
    Python-level mutex) must never both be inside the critical section at
    the same time."""

    def test_two_threads_never_overlap_inside_the_critical_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            currently_inside = []
            max_concurrent_seen = []
            lock_for_shared_state = threading.Lock()

            def worker():
                with exclusive_ledger_lock(ledger_path, timeout=10):
                    with lock_for_shared_state:
                        currently_inside.append(1)
                        max_concurrent_seen.append(len(currently_inside))
                    time.sleep(0.1)
                    with lock_for_shared_state:
                        currently_inside.pop()

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            self.assertEqual(max(max_concurrent_seen), 1, "two holders were inside the critical section at once")


class TwoProcessesCompetingTests(unittest.TestCase):
    """The authoritative cross-PROCESS proof (as opposed to
    TwoThreadsCompetingTests' same-process threads): a real, separate OS
    process holds the lock while this process attempts to acquire it,
    confirming genuine cross-process exclusion on whichever backend this
    platform dispatches to."""

    def test_a_separate_process_holding_the_lock_blocks_this_process_until_it_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            ctx = multiprocessing.get_context("spawn")
            ready = ctx.Event()
            release = ctx.Event()
            proc = ctx.Process(target=_hold_lock_until_released, args=(str(ledger_path), ready, release))
            proc.start()
            try:
                self.assertTrue(ready.wait(timeout=10), "subprocess never signaled that it holds the lock")

                acquired_after_release = []

                def try_acquire_in_main_process():
                    with exclusive_ledger_lock(ledger_path, timeout=10):
                        acquired_after_release.append(time.monotonic())

                t = threading.Thread(target=try_acquire_in_main_process)
                t.start()
                time.sleep(0.3)  # give the main-process attempt time to genuinely start blocking
                self.assertEqual(acquired_after_release, [], "main process acquired the lock while the subprocess still held it")

                release.set()
                t.join(timeout=10)
                self.assertEqual(len(acquired_after_release), 1, "main process never acquired the lock after the subprocess released it")
            finally:
                release.set()
                proc.join(timeout=10)
                if proc.is_alive():  # pragma: no cover -- only on a genuine hang
                    proc.terminate()


def _hold_lock_until_released(ledger_path_str: str, ready, release) -> None:
    """Module-level (picklable, importable by name -- required for
    ``multiprocessing`` under the ``spawn`` start method, which every
    Windows Python uses) target run in a genuinely separate process:
    acquires the lock, signals ``ready``, then holds it until ``release``
    is set."""

    from pathlib import Path

    from ledgers.locking import exclusive_ledger_lock

    with exclusive_ledger_lock(Path(ledger_path_str), timeout=10):
        ready.set()
        release.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
