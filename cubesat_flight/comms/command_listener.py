"""Background daemon thread for GCS -> CubeSat commands.

Listens on COMMAND_PORT for newline-delimited JSON commands. Commands go into a
thread-safe queue that the state machine drains at safe points via get_pending().
The listener accepts a new connection after each one closes, so the GCS can reconnect
between passes without restarting the CubeSat software.
"""

import json
import queue
import socket
import threading

from config import COMMAND_PORT
from protocol import ACK, NACK

# Commands accepted into the queue; anything else is NACKed.
_KNOWN_COMMANDS = {
    "retransmit",
    "priority_cell",
    "set_cell",
    "adjust_exposure",
    "enter_safe_mode",
    "resume_normal",
    "status_request",
    "retry_downlink",
    "start_pass",
    "end_pass",
    "cell",
}


class CommandListener:
    """Receives and queues ground-station commands."""

    def __init__(self) -> None:
        self._queue = queue.Queue()
        self._thread = None

    def start(self) -> None:
        """Start the background daemon thread; call once at boot."""
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="CommandListener")
        self._thread.start()

    def get_pending(self) -> list[dict]:
        """Drain and return all commands received since the last call."""
        commands = []
        while True:
            try:
                commands.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return commands

    def _accept_loop(self) -> None:
        """Bind the server socket and serve connections one at a time."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("", COMMAND_PORT))
        server.listen(1)

        while True:
            try:
                conn, _addr = server.accept()
                self._handle_connection(conn)
            except OSError:
                # Server socket closed; the daemon thread exits.
                break

    def _handle_connection(self, conn: socket.socket) -> None:
        """Read newline-delimited JSON commands until the GCS closes or the socket errors."""
        buf = b""
        try:
            conn.settimeout(5.0)
            while True:
                data = conn.recv(4096)
                if not data:
                    break  # GCS closed the connection cleanly
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    ok = self._parse_and_queue(line)
                    try:
                        conn.sendall(ACK if ok else NACK)
                    except OSError:
                        pass
        except OSError:
            pass  # Socket error — connection dropped
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _parse_and_queue(self, line_bytes: bytes) -> bool:
        """Queue one JSON command line; return False if it is malformed or unknown."""
        try:
            cmd = json.loads(line_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False  # Malformed command

        if not isinstance(cmd, dict) or "cmd" not in cmd:
            return False  # Not a command dict

        if cmd["cmd"] not in _KNOWN_COMMANDS:
            return False  # Unknown command

        self._queue.put(cmd)
        return True
