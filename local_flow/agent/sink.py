"""AgentSink: a TextSink that sends dictation to poppy-desk over local IPC."""

from __future__ import annotations

import json
import logging
import os
import socket
import stat
import struct
import sys
from pathlib import Path

from local_flow.errors import ConfigError, LocalFlowError
from local_flow.insertion.base import TextSink

logger = logging.getLogger(__name__)

# Unix domain socket sun_path limit (macOS: 104 bytes, Linux: 108 bytes, we use the smaller limit)
_MAX_SOCKET_PATH_LENGTH = 104


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
    """Routes ``insert`` to poppy-desk via Unix domain socket.

    IPC message format (JSON, UTF-8 encoded, newline-terminated):

    .. code-block:: json

        {"version": 1, "text": "the dictated command"}

    The ``version`` field enables future protocol extensions. Only ``version: 1``
    is currently defined.

    ``press_key`` is a no-op: agent commands are sent as-is with no key
    simulation. The agent hotkey is for sending commands to an autonomous
    agent, not for dictating keystrokes into a document.

    Connection errors (poppy-desk not running, socket unavailable) raise
    LocalFlowError and are logged as warnings. Each ``insert`` call
    attempts one connection; no retries, no persistent connection pool.

    **Security invariant**: only local Unix domain sockets are supported.

    The socket's parent directory must be owned by the current user, must be
    a real directory (not a symlink), and must have mode exactly 0700.
    The socket itself must be a socket (not a symlink or regular file) and
    owned by the current user. After connecting, the peer UID is verified
    to match the current user. All checks use lstat to prevent symlink races.

    These checks happen before every connection attempt. A socket that passes
    validation at construction time but later becomes compromised (ownership
    changed, moved to a world-writable directory) will be rejected at send time.
    """

    name = "agent"

    def __init__(self, target: str) -> None:
        """
        :param target: ``unix:/path/to/socket`` or just ``/path/to/socket``
        :raises ConfigError: if ``target`` is not a valid Unix socket path
        """
        self.target = target
        self._validate_target_format()

    def _validate_target_format(self) -> None:
        """Ensure ``target`` has a valid format (detailed security checks happen at send time)."""
        # Strip unix: prefix if present
        path_str = self.target[5:] if self.target.startswith("unix:") else self.target

        if not path_str:
            raise ConfigError(
                "agent_target path cannot be empty.",
                hint=(
                    "Use unix:/path/to/socket or /path/to/socket "
                    f"(e.g. unix:{_get_default_agent_socket_path()})"
                ),
            )

        # Check for null bytes (would cause ValueError at connect time)
        if "\x00" in path_str:
            raise ConfigError(
                "agent_target path contains null byte.",
                hint="Socket paths cannot contain null bytes.",
            )

        # Expand ~ and check if relative
        try:
            expanded = Path(path_str).expanduser()
        except Exception as exc:
            raise ConfigError(
                f"agent_target path {path_str!r} cannot be expanded: {exc}",
            ) from exc

        # Reject relative paths (require absolute paths for security)
        if not expanded.is_absolute():
            raise ConfigError(
                f"agent_target path must be absolute; got relative path {path_str!r}.",
                hint="Use an absolute path like /path/to/socket or ~/path/to/socket.",
            )

        # Resolve to check sun_path limit
        try:
            resolved = expanded.resolve()
        except Exception as exc:
            raise ConfigError(
                f"agent_target path {path_str!r} cannot be resolved: {exc}",
            ) from exc

        resolved_str = str(resolved)
        if len(resolved_str.encode("utf-8")) >= _MAX_SOCKET_PATH_LENGTH:
            raise ConfigError(
                f"agent_target path is too long ({len(resolved_str.encode('utf-8'))} bytes, "
                f"max {_MAX_SOCKET_PATH_LENGTH - 1}).",
                hint="Unix domain socket paths have a 104-byte limit. Use a shorter path.",
            )

        # Reject paths directly in /tmp or /private/tmp (macOS) or /private/var/tmp
        # Compare resolved paths so macOS symlinks are caught
        tmp_resolved = Path("/tmp").resolve()
        var_tmp_resolved = Path("/var/tmp").resolve() if Path("/var/tmp").exists() else None

        # Also check /private/tmp and /private/var/tmp directly (macOS canonical paths)
        private_tmp = Path("/private/tmp") if Path("/private/tmp").exists() else None
        private_var_tmp = Path("/private/var/tmp") if Path("/private/var/tmp").exists() else None

        # Check if socket path itself is /tmp or if its parent is /tmp
        if resolved == tmp_resolved or resolved.parent == tmp_resolved:
            raise ConfigError(
                f"agent_target path {path_str!r} is /tmp or directly in /tmp, "
                "which is world-writable.",
                hint="Use a private subdirectory like /tmp/myapp/ or a directory in your home.",
            )
        # Also check if path is in /private/tmp (macOS canonical path)
        elif private_tmp and (resolved == private_tmp or resolved.parent == private_tmp):
            raise ConfigError(
                f"agent_target path {path_str!r} is /private/tmp or directly in /private/tmp, "
                "which is world-writable.",
                hint="Use a private subdirectory or a directory in your home.",
            )
        # Check /var/tmp
        elif var_tmp_resolved and (
            resolved == var_tmp_resolved or resolved.parent == var_tmp_resolved
        ):
            raise ConfigError(
                f"agent_target path {path_str!r} is /var/tmp or directly in /var/tmp, "
                "which is world-writable.",
                hint="Use a private subdirectory or a directory in your home.",
            )
        # Also check /private/var/tmp (macOS canonical path)
        elif private_var_tmp and (
            resolved == private_var_tmp or resolved.parent == private_var_tmp
        ):
            raise ConfigError(
                f"agent_target path {path_str!r} is /private/var/tmp or "
                "directly in /private/var/tmp, which is world-writable.",
                hint="Use a private subdirectory or a directory in your home.",
            )

    def _validate_socket_security(self, socket_path: Path) -> None:
        """Verify socket and parent directory are owned by current user with secure permissions.

        Uses lstat to prevent symlink races. Checks parent is a real directory (not symlink),
        owned by current user, with mode exactly 0700. Checks socket is a real socket (not
        symlink or regular file), owned by current user.

        :raises LocalFlowError: if validation fails (wrong owner, world-writable parent, etc.)
        """
        current_uid = os.getuid()

        # Socket must exist (use lstat to detect symlinks)
        try:
            socket_stat = socket_path.lstat()
        except FileNotFoundError as exc:
            raise LocalFlowError(
                f"agent socket does not exist: {socket_path}",
                hint="Make sure poppy-desk is running and has created the socket.",
            ) from exc
        except OSError as exc:
            raise LocalFlowError(
                f"agent socket cannot be accessed: {socket_path}: {exc}",
            ) from exc

        # Socket must be a real socket, not a symlink or regular file
        if not stat.S_ISSOCK(socket_stat.st_mode):
            raise LocalFlowError(
                f"agent socket path is not a socket: {socket_path}",
                hint="The path must be a Unix domain socket, not a regular file or symlink.",
            )

        # Socket itself must be owned by current user
        if socket_stat.st_uid != current_uid:
            raise LocalFlowError(
                f"agent socket is owned by uid {socket_stat.st_uid}, "
                f"not current user ({current_uid})",
                hint="The socket must be owned by you. Remove it and restart poppy-desk.",
            )

        # Parent directory must exist and be a real directory
        parent_dir = socket_path.parent
        try:
            parent_stat = parent_dir.lstat()
        except FileNotFoundError as exc:
            raise LocalFlowError(
                f"agent socket parent directory does not exist: {parent_dir}",
            ) from exc
        except OSError as exc:
            raise LocalFlowError(
                f"agent socket parent directory cannot be accessed: {parent_dir}: {exc}",
            ) from exc

        # Parent must be a real directory, not a symlink
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise LocalFlowError(
                f"agent socket parent is not a directory: {parent_dir}",
                hint="The parent path must be a real directory, not a symlink or file.",
            )

        # Parent directory must be owned by current user
        if parent_stat.st_uid != current_uid:
            raise LocalFlowError(
                f"agent socket parent directory is owned by uid {parent_stat.st_uid}, "
                f"not current user ({current_uid})",
                hint="The socket directory must be owned by you for security.",
            )

        # Parent directory must have mode exactly 0700 (no group or world perms)
        mode = stat.S_IMODE(parent_stat.st_mode)
        if mode != 0o700:
            raise LocalFlowError(
                f"agent socket parent directory {parent_dir} has mode {oct(mode)}, not 0700",
                hint=(
                    "The socket directory must have mode 0700 (chmod 700) "
                    "to prevent command interception."
                ),
            )

        # Check all ancestors up to root (T2: prevent writable ancestor attacks)
        # Walk up the directory tree and ensure each ancestor is:
        # 1. Not group/world writable (unless sticky bit set), AND
        # 2. Owned by root or current user (non-root users can rename directories they own)
        current_dir = parent_dir.parent
        checked_dirs = set()  # Avoid infinite loops on .. or symlinks
        while current_dir != current_dir.parent:  # Stop at root
            if current_dir in checked_dirs:
                break
            checked_dirs.add(current_dir)

            try:
                dir_stat = current_dir.lstat()
            except (FileNotFoundError, OSError) as exc:
                # Can't stat ancestor - treat as security failure
                raise LocalFlowError(
                    f"agent socket ancestor directory {current_dir} cannot be accessed: {exc}",
                    hint="Socket path ancestors must be readable and secure.",
                ) from exc

            # Check ownership: must be owned by root (0) or current user
            if dir_stat.st_uid not in (0, current_uid):
                raise LocalFlowError(
                    f"agent socket ancestor directory {current_dir} "
                    f"is owned by uid {dir_stat.st_uid} "
                    f"(not root or current user {current_uid})",
                    hint="Socket path ancestors must be owned by you or root for security.",
                )

            # Check if group/world writable without sticky bit
            dir_mode = stat.S_IMODE(dir_stat.st_mode)
            has_group_write = bool(dir_mode & stat.S_IWGRP)
            has_world_write = bool(dir_mode & stat.S_IWOTH)
            has_sticky = bool(dir_mode & stat.S_ISVTX)

            # Reject if writable by others without sticky bit
            if (has_group_write or has_world_write) and not has_sticky:
                raise LocalFlowError(
                    f"agent socket ancestor directory {current_dir} is writable by others "
                    f"(mode {oct(dir_mode)}) without sticky bit",
                    hint="Socket path ancestors must be either not group/world writable, "
                    "or have the sticky bit set (like /tmp).",
                )

            current_dir = current_dir.parent

    def _verify_peer_uid(self, sock: socket.socket) -> None:
        """Verify the connected peer UID matches current user (where platform supports it).

        :raises LocalFlowError: if peer UID check fails or mismatches
        """
        current_uid = os.getuid()

        # Platform-specific peer credential checks
        if sys.platform == "darwin":
            # macOS: LOCAL_PEERCRED returns xucred structure
            # CPython defines LOCAL_PEERCRED but not SOL_LOCAL, so use getattr
            sol_local = getattr(socket, "SOL_LOCAL", 0)  # SOL_LOCAL is 0 on Darwin
            local_peercred = getattr(socket, "LOCAL_PEERCRED", 0x001)
            try:
                creds = sock.getsockopt(sol_local, local_peercred, 76)  # Request full xucred
                # Check we got at least 8 bytes (version + uid)
                if len(creds) < 8:
                    raise LocalFlowError(
                        f"agent socket peer credential reply too short ({len(creds)} bytes)",
                        hint=(
                            "Cannot verify peer identity. "
                            "Make sure poppy-desk is running correctly."
                        ),
                    )
                # xucred: uint32_t cr_version, uid_t cr_uid, ...
                cr_version, peer_uid = struct.unpack("=II", creds[:8])
                # Check version is 0 (XUCRED_VERSION)
                if cr_version != 0:
                    raise LocalFlowError(
                        f"agent socket peer credential version {cr_version} not supported",
                        hint=(
                            "Cannot verify peer identity. "
                            "Make sure poppy-desk is running correctly."
                        ),
                    )
                if peer_uid != current_uid:
                    raise LocalFlowError(
                        f"agent socket peer is owned by uid {peer_uid}, "
                        f"not current user ({current_uid})",
                        hint="The socket peer must be your process. Restart poppy-desk.",
                    )
            except OSError as exc:
                # Credential check failed - fail closed (reject the connection)
                raise LocalFlowError(
                    f"agent socket peer credential check failed: {exc}",
                    hint="Cannot verify peer identity. Make sure poppy-desk is running correctly.",
                ) from exc
        elif sys.platform.startswith("linux"):
            # Linux: SO_PEERCRED returns ucred structure
            try:
                so_peercred = socket.SO_PEERCRED
                creds = sock.getsockopt(socket.SOL_SOCKET, so_peercred, struct.calcsize("3i"))
                peer_pid, peer_uid, peer_gid = struct.unpack("3i", creds)
                if peer_uid != current_uid:
                    raise LocalFlowError(
                        f"agent socket peer is owned by uid {peer_uid}, "
                        f"not current user ({current_uid})",
                        hint="The socket peer must be your process. Restart poppy-desk.",
                    )
            except OSError as exc:
                # Credential check failed - fail closed (reject the connection)
                raise LocalFlowError(
                    f"agent socket peer credential check failed: {exc}",
                    hint="Cannot verify peer identity. Make sure poppy-desk is running correctly.",
                ) from exc

    def insert(self, text: str) -> None:
        """Send ``text`` to poppy-desk as a JSON message over the configured Unix socket.

        :raises LocalFlowError: if connection or send fails
        """
        if not text:
            # Empty text - nothing to send
            logger.warning("agent: insert called with empty text, nothing sent")
            return

        message = {"version": 1, "text": text}
        payload = json.dumps(message).encode("utf-8") + b"\n"

        try:
            self._send_unix(payload)
        except OSError as exc:
            # Convert OSError to LocalFlowError with a clear message
            logger.warning(
                "agent: failed to send command to poppy-desk at %s: %s",
                self.target,
                str(exc),
            )
            raise LocalFlowError(
                f"failed to send command to poppy-desk at {self.target}",
                hint="Make sure poppy-desk is running and the socket path is correct.",
            ) from exc
        except LocalFlowError as exc:
            # LocalFlowError already has good messages, log the full details
            logger.warning(
                "agent: failed to send command to poppy-desk at %s: %s",
                self.target,
                exc.message,
            )
            raise

    def _send_unix(self, payload: bytes) -> None:
        # Strip unix: prefix if present
        path_str = self.target[5:] if self.target.startswith("unix:") else self.target
        socket_path = Path(path_str).expanduser().resolve()

        # Security validation before connection (using the same resolved path)
        self._validate_socket_security(socket_path)

        # Connect using the resolved path (same path that was validated)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(2.0)
            sock.connect(str(socket_path))

            # Re-validate post-connect to detect TOCTOU attacks (T1)
            # If the directory was swapped after validation but before connect,
            # this will catch it
            self._validate_socket_security(socket_path)

            # Verify peer UID after connection (protects against other-UID attackers)
            self._verify_peer_uid(sock)

            sock.sendall(payload)
        finally:
            sock.close()

    def press_key(self, key: str) -> None:
        """Log warning when key actions are called (agent commands don't support keystrokes)."""
        logger.warning(
            "agent: key action %r requested but agent commands don't support keystrokes",
            key,
        )
