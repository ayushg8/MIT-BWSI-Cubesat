"""Throttled TCP client for CubeSat -> GCS transfers.

Image data goes out in THROTTLE_BYTES_PER_SEC (1200 byte) chunks with a real
time.sleep(1.0) between chunks, simulating a 9600-baud UHF link (a 28 KB image takes
about 23 s).

After MAX_GCS_CONNECT_FAILURES consecutive connection failures, connect() raises
GCSUnreachableError so the main loop can suspend downlink instead of burning the
whole window on timeouts.
"""

import json
import socket
import time

from comms.packet import build_image_header, build_telemetry_header
from config import (
    ACK_TIMEOUT_SEC,
    DATA_PORT,
    GROUND_STATION_IP,
    MAX_GCS_CONNECT_FAILURES,
    THROTTLE_BYTES_PER_SEC,
)
from protocol import ACK, NACK


class GCSUnreachableError(Exception):
    """Raised when consecutive connection failures reach MAX_GCS_CONNECT_FAILURES."""


class Transfer:
    """One TCP link to the ground station with failure tracking."""

    def __init__(self) -> None:
        self._sock = None
        self.consecutive_failures = 0
        self.bytes_sent = 0  # Cumulative bytes sent this downlink window
        self.last_error = None  # Last error string ("NACK", "timeout", etc.)

    def connect(self) -> None:
        """Open a TCP connection to the GCS DATA_PORT.

        Resets consecutive_failures on success; raises GCSUnreachableError once
        MAX_GCS_CONNECT_FAILURES is reached.
        """
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(ACK_TIMEOUT_SEC)
            self._sock.connect((GROUND_STATION_IP, DATA_PORT))
            self.consecutive_failures = 0
        except OSError as exc:
            self._close_socket()
            self.consecutive_failures += 1
            self.last_error = f"connect_failed: {exc}"
            if self.consecutive_failures >= MAX_GCS_CONNECT_FAILURES:
                raise GCSUnreachableError(f"GCS unreachable after {self.consecutive_failures} attempts") from exc
            raise

    def disconnect(self) -> None:
        """Shut the socket down gracefully, then close it."""
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._close_socket()

    def force_disconnect(self) -> None:
        """Close immediately with no shutdown handshake (link failure)."""
        self._close_socket()

    def send_telemetry(self, telem_dict: dict) -> bool:
        """Send a telemetry packet and wait for ACK/NACK; True on ACK.

        At ~500 B it fits in one chunk, so there is no throttle sleep.
        """
        header, payload = build_telemetry_header(telem_dict)
        try:
            self._send_header(header)
            self._sock.sendall(payload)
            self.bytes_sent += len(payload)
            return self._wait_for_ack()
        except OSError as exc:
            self.last_error = f"socket_error: {exc}"
            self.force_disconnect()
            return False

    def send_file(self, filepath: str, metadata: dict, watchdog=None) -> bool:
        """Send one image as header + throttled chunks, then wait for ACK/NACK; True on ACK.

        The watchdog is petted every chunk so long transfers do not trip it.
        On failure last_error is "NACK", "timeout" or "socket_error: ...".
        """
        header = build_image_header(filepath, metadata)
        try:
            self._send_header(header)
        except OSError as exc:
            self.last_error = f"socket_error: {exc}"
            self.force_disconnect()
            return False

        try:
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(THROTTLE_BYTES_PER_SEC)
                    if not chunk:
                        break
                    self._sock.sendall(chunk)
                    self.bytes_sent += len(chunk)
                    if watchdog is not None:
                        watchdog.pet()
                    time.sleep(1.0)  # real sleep: simulates the UHF link rate
        except OSError as exc:
            self.last_error = f"socket_error: {exc}"
            self.force_disconnect()
            return False

        return self._wait_for_ack()

    def _send_header(self, header_dict: dict) -> None:
        """Send the header as newline-terminated JSON."""
        line = json.dumps(header_dict, default=str) + "\n"
        self._sock.sendall(line.encode("utf-8"))

    def _wait_for_ack(self) -> bool:
        """Read one ACK/NACK byte; True on ACK. Sets last_error on failure."""
        try:
            self._sock.settimeout(ACK_TIMEOUT_SEC)
            byte = self._sock.recv(1)
            if byte == ACK:
                self.last_error = None
                return True
            if byte == NACK:
                self.last_error = "NACK"
                return False
            self.last_error = f"unexpected_byte: {byte!r}"
            return False
        except TimeoutError:
            self.last_error = "timeout"
            return False
        except OSError as exc:
            self.last_error = f"socket_error: {exc}"
            return False

    def _close_socket(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def send_telemetry_now(telem_dict: dict) -> bool:
    """Send one telemetry packet over a fresh connection; True on ACK, False if the GCS is unreachable."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(ACK_TIMEOUT_SEC)
        sock.connect((GROUND_STATION_IP, DATA_PORT))
        header, payload = build_telemetry_header(telem_dict)
        line = json.dumps(header, default=str) + "\n"
        sock.sendall(line.encode("utf-8"))
        sock.sendall(payload)
        byte = sock.recv(1)
        return byte == ACK
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass
