"""AgentSink: a TextSink that sends dictation to poppy-desk over local IPC."""

from __future__ import annotations

import json
import logging
import os
import socket
import stat
from pathlib import Path

from local_flow.errors import ConfigError, LocalFlowError
from local_flow.insertion.base import TextSink

logger = logging.getLogger(__name__)


def _get_default_agent_socket_path() -> str:
    """Return the default agent socket path in a user-owned private directory.

    macOS: ~/Library/Application Support/poppy-desk/agent.sock
    Linux: $XDG_RUNTIME_DIR/poppy-desk/agent.sock (fallback: ~/.local/state/poppy-desk/agent.sock)
    """
    import platform

    if platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
        if xdg_runtime:
            base = Path(xdg_runtime)
        else:
            base = Path.home() / ".local" / "state"

    return str(base / "poppy-desk" / "agent.sock")


class AgentSink(TextSink):
    """Routes ``insert`` to poppy-desk via Unix domain socket or localhost TCP.

    IPC message format (JSON, UTF-8 encoded, newline-terminated):

    .. code-block:: json

        {"version": 1, "text": "the dictated command"}

    The ``version`` field enables future protocol extensions. Only ``version: 1``
    is currently defined.

    ``press_key`` is a no-op: agent commands are sent as-is with no key
    simulation. The agent hotkey is for sending commands to an autonomous
    agent, not for dictating keystrokes into a document.

    Connection errors (poppy-desk not running, socket unavailable) are logged
    as warnings; they never crash the dictation session. Each ``insert`` call
    attempts one connection; no retries, no persistent connection pool.

    **Security invariant**: only secure local IPC is allowed.

    - **Unix sockets**: The socket's parent directory must be owned by the
      current user and not group/world writable (mode 0700-style). The socket
      itself must be owned by the current user. This prevents command
      interception by other users or processes that can write to shared
      directories like ``/tmp``.

    - **TCP**: Only ``127.0.0.1`` (localhost) is allowed; any other host is
      rejected at construction time with :exc:`ConfigError`.

    These checks happen before every connection attempt. A socket that passes
    validation at construction time but later becomes compromised (ownership
    changed, moved to a world-writable directory) will be rejected at send time.
    """

    name = "agent"

    def __init__(self, target: str) -> None:
        """
        :param target: ``unix:/path/to/socket`` or ``tcp://127.0.0.1:PORT``
        :raises ConfigError: if ``target`` is not a secure loopback/local-only address
        """
        self.target = target
        self._validate_target_format()

    def _validate_target_format(self) -> None:
        """Ensure ``target`` has a valid format (detailed security checks happen at send time)."""
        if self.target.startswith("unix:"):
            # Format check only; ownership/permissions validated before each connection
            path_str = self.target[5:]
            if not path_str:
                raise ConfigError(
                    "agent_target unix: path cannot be empty.",
                    hint="Use unix:/path/to/socket (e.g. unix:~/Library/Application Support/poppy-desk/agent.sock)",
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
                hint="Use unix:/path/to/socket or tcp://127.0.0.1:PORT.",
            )

    def _validate_socket_security(self, socket_path: Path) -> None:
        """Verify socket and parent directory are owned by current user with secure permissions.

        :raises LocalFlowError: if validation fails (wrong owner, world-writable parent, etc.)
        """
        current_uid = os.getuid()

        # Socket must exist
        if not socket_path.exists():
            raise LocalFlowError(
                f"agent socket does not exist: {socket_path}",
                hint="Make sure poppy-desk is running and has created the socket.",
            )

        # Socket itself must be owned by current user
        socket_stat = socket_path.stat()
        if socket_stat.st_uid != current_uid:
            raise LocalFlowError(
                f"agent socket is owned by uid {socket_stat.st_uid}, not current user ({current_uid})",
                hint="The socket must be owned by you. Remove it and restart poppy-desk.",
            )

        # Parent directory must be owned by current user
        parent_dir = socket_path.parent
        if not parent_dir.exists():
            raise LocalFlowError(
                f"agent socket parent directory does not exist: {parent_dir}",
            )

        parent_stat = parent_dir.stat()
        if parent_stat.st_uid != current_uid:
            raise LocalFlowError(
                f"agent socket parent directory is owned by uid {parent_stat.st_uid}, not current user ({current_uid})",
                hint="The socket directory must be owned by you for security.",
            )

        # Parent directory must not be group or world writable
        mode = stat.S_IMODE(parent_stat.st_mode)
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise LocalFlowError(
                f"agent socket parent directory {parent_dir} is group or world writable (mode {oct(mode)})",
                hint="The socket directory must be private (chmod 700) to prevent command interception.",
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
        except (OSError, LocalFlowError) as exc:
            message_str = getattr(exc, "message", str(exc))
            logger.warning(
                "agent: failed to send command to poppy-desk at %s: %s",
                self.target,
                message_str,
            )

    def _send_unix(self, payload: bytes) -> None:
        path_str = self.target[5:]
        socket_path = Path(path_str).expanduser().resolve()

        # Security validation before every connection
        self._validate_socket_security(socket_path)

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(str(socket_path))
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
