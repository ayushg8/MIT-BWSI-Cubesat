"""Flight software logger.

Named logger.py (not logging.py) to avoid clashing with the stdlib logging module.

Line format: {ISO_timestamp} [{STATE}] [{LEVEL}] {message}, e.g.
    2026-03-15T14:45:00Z [IMAGING] [INFO] STABLE + NADIR — capturing

Lines are appended to LOG_DIR/flight.log (rotated at 5 MB) and the last 50 are kept in
memory so build_telemetry() can include recent_log in telemetry packets.

Usage:
    from utils.logger import log
    log("something happened")
    log("debug detail", level="DEBUG")
"""

import collections
import os
import threading
from datetime import UTC, datetime

from config import LOG_DIR

_LOG_FILE = os.path.join(LOG_DIR, "flight.log")
_MAX_LOG_FILE_BYTES = 5 * 1024 * 1024  # 5 MB before rotation
_MEMORY_LINES = 50


class Logger:
    """Thread-safe file + in-memory logger."""

    def __init__(self) -> None:
        self._state = "BOOT"
        self._recent = collections.deque(maxlen=_MEMORY_LINES)
        self._lock = threading.Lock()
        os.makedirs(LOG_DIR, exist_ok=True)
        self._file = open(_LOG_FILE, "a", buffering=1)  # line-buffered

    def set_state(self, state: str) -> None:
        """Update the state label prepended to every subsequent log line."""
        self._state = state.upper()

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def debug(self, message: str) -> None:
        self._write("DEBUG", message)

    def warn(self, message: str) -> None:
        self._write("WARN", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)

    def get_recent(self) -> list[str]:
        """Return the most recent log lines, oldest first."""
        with self._lock:
            return list(self._recent)

    def _write(self, level: str, message: str) -> None:
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{timestamp} [{self._state}] [{level}] {message}"
        with self._lock:
            self._recent.append(line)
            try:
                self._file.write(line + "\n")
                self._rotate_if_needed()
            except OSError:
                pass  # Don't crash the flight software on a logging error

    def _rotate_if_needed(self) -> None:
        """Rotate the log file once it exceeds _MAX_LOG_FILE_BYTES."""
        try:
            if os.path.getsize(_LOG_FILE) < _MAX_LOG_FILE_BYTES:
                return
            self._file.close()
            rotated = _LOG_FILE + ".1"
            if os.path.exists(rotated):
                os.remove(rotated)
            os.rename(_LOG_FILE, rotated)
            self._file = open(_LOG_FILE, "a", buffering=1)
        except OSError:
            pass


_instance = Logger()


def log(message: str, level: str = "INFO") -> None:
    """Write a line to the singleton logger; level is DEBUG, INFO, WARN or ERROR."""
    _instance._write(level, message)


def set_state(state: str) -> None:
    """Set the state label on the singleton logger."""
    _instance.set_state(state)


def get_recent() -> list[str]:
    """Return recent log lines from the singleton logger."""
    return _instance.get_recent()


def get_logger() -> Logger:
    """Return the singleton Logger."""
    return _instance
