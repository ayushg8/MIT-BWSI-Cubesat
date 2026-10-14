"""Software watchdog.

The main loop must call pet() regularly. If pet() is not called within
WATCHDOG_TIMEOUT_SEC (30 s), the watchdog saves recovery state and restarts the process
with os.execv(), equivalent to a clean reboot. On restart main.py reads the recovery file
and resumes from the last known pass number instead of starting fresh.
"""

import os
import sys
import threading
import time
from collections.abc import Callable

from config import WATCHDOG_CHECK_INTERVAL_SEC, WATCHDOG_TIMEOUT_SEC
from utils.logger import log


class Watchdog:
    """Restarts the process if the main loop stops petting it."""

    def __init__(self) -> None:
        self._last_pet = time.monotonic()
        self._lock = threading.Lock()
        self._thread = None
        self._recovery_callback = None  # Called with no args before restart

    def start(self, recovery_callback: Callable[[], None] | None = None) -> None:
        """Start the daemon thread; recovery_callback runs just before restart to flush state to disk."""
        self._recovery_callback = recovery_callback
        self._last_pet = time.monotonic()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True, name="Watchdog")
        self._thread.start()

    def pet(self) -> None:
        """Reset the watchdog timer."""
        with self._lock:
            self._last_pet = time.monotonic()

    def _watch_loop(self) -> None:
        while True:
            time.sleep(WATCHDOG_CHECK_INTERVAL_SEC)
            with self._lock:
                elapsed = time.monotonic() - self._last_pet
            if elapsed > WATCHDOG_TIMEOUT_SEC:
                self._trigger()

    def _trigger(self) -> None:
        """Save recovery state and restart the process."""
        log(
            f"WATCHDOG TRIGGERED — no pet for >{WATCHDOG_TIMEOUT_SEC}s. Saving recovery state and restarting.",
            level="ERROR",
        )
        if self._recovery_callback is not None:
            try:
                self._recovery_callback()
            except Exception as exc:
                log(f"Recovery callback failed: {exc}", level="ERROR")

        # Replace this process with a fresh copy of main.py.
        os.execv(sys.executable, [sys.executable] + sys.argv)
