"""AgentSink: a TextSink that sends dictation to poppy-desk over local IPC."""

from __future__ import annotations

import json
import logging
import socket
from pathlib import Path

from local_flow.errors import ConfigError
from local_flow.insertion.base import TextSink

logger = logging.getLogger(__name__)


class AgentSink(TextSink):
    """Routes ``insert`` to poppy-desk via Unix domain socket or localhost TCP.

    IPC message format (JSON, UTF-8 encoded, newline-terminated):

    .. code-block:: json

        {"version": 1, "text": "the dictated command"}

    The ``version`` field enables future protocol extensions without breaking
    existing receivers. Only ``version: 1`` is currently defined.

    ``press_key`` is a no-op: agent commands are sent as-is with no key
    simulation. The agent hotkey is for sending commands to an autonomous
    agent, not for dictating keystrokes into a document.

    Connection errors (poppy-desk not running, socket unavailable) are logged
    as warnings; they never crash the dictation session. Each ``insert`` call
    attempts one connection; no retries, no persistent connection pool.

    **Security invariant**: only local IPC is allowed. Any socket path outside
    ``/tmp`` or ``/var/run`` (Unix) or any TCP target not on ``127.0.0.1``
    (localhost) is rejected at construction time with :exc:`ConfigError`. This
    prevents accidental credential/command leaks to network endpoints.
    """

    name = "agent"

    def __init__(self, target: str) -> None:
        """
        :param target: ``unix:/path/to/socket`` or ``tcp://127.0.0.1:PORT``
        :raises ConfigError: if ``target`` is not a loopback/local-only address
        """
        self.target = target
        self._validate_target()

    def _validate_target(self) -> None:
        """Ensure ``target`` is local-only (Unix socket or localhost TCP)."""
        if self.target.startswith("unix:"):
            path_str = self.target[5:]
            path = Path(path_str).resolve()
            allowed_parents = [Path("/tmp"), Path("/var/run")]
            if not any(
                path == parent or parent in path.parents for parent in allowed_parents
            ):
                raise ConfigError(
                    f"agent_target must use /tmp or /var/run; got {path_str!r}.",
                    hint="Only local Unix sockets are allowed for security. "
                    "Use unix:/tmp/poppy-desk.sock or similar.",
                )
        elif self.target.startswith("tcp://"):
            rest = self.target[6:]
            host, _, port_str = rest.partition(":")
            if host != "127.0.0.1":
                raise ConfigError(
                    f"agent_target TCP host must be 127.0.0.1; got {host!r}.",
                    hint="Only localhost is allowed for security.",
                )
            if not port_str or not port_str.isdigit():
                raise ConfigError(
                    f"agent_target TCP format is tcp://127.0.0.1:PORT; got {self.target!r}.",
                )
        else:
            raise ConfigError(
                f"agent_target must start with unix: or tcp://; got {self.target!r}.",
                hint='Use unix:/tmp/poppy-desk.sock or tcp://127.0.0.1:9876.',
            )

    def insert(self, text: str) -> None:
        """Send ``text`` to poppy-desk as a JSON message over the configured IPC.

        Logs warnings on connection/send failures; never raises.
        """
        message = {"version": 1, "text": text}
        payload = json.dumps(message).encode("utf-8") + b"\n"

        try:
            if self.target.startswith("unix:"):
                self._send_unix(payload)
            elif self.target.startswith("tcp://"):
                self._send_tcp(payload)
        except OSError as exc:
            logger.warning(
                "agent: failed to send command to poppy-desk at %s: %s",
                self.target,
                exc,
            )

    def _send_unix(self, payload: bytes) -> None:
        path = self.target[5:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(path)
            sock.sendall(payload)

    def _send_tcp(self, payload: bytes) -> None:
        rest = self.target[6:]
        host, _, port_str = rest.partition(":")
        port = int(port_str)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect((host, port))
            sock.sendall(payload)

    def press_key(self, key: str) -> None:
        """No-op: agent commands are sent as-is, not as simulated keystrokes."""
        pass
