"""Cross-process ownership for the one live dictation runtime.

The native app host, the legacy ``local-flow run`` command, and the tray
surface all own the same global hotkey and microphone.  Running more than one
at a time creates duplicate overlays and competing insertions, so they share a
non-blocking lock under the configured local data directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO

from local_flow.errors import LocalFlowError


class RuntimeInstanceLock:
    """Hold one per-user runtime lock until :meth:`release` or context exit."""

    def __init__(self, data_dir: Path, owner: str) -> None:
        self.path = data_dir / "runtime.lock"
        self.owner = owner
        self._handle: IO[bytes] | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            self._lock(handle)
        except (BlockingIOError, OSError) as exc:
            handle.close()
            raise LocalFlowError(
                "Another JiSpr dictation runtime is already active.",
                hint=(
                    "Quit the other JiSpr/local-flow window or background command, "
                    "then open JiSpr again."
                ),
            ) from exc
        self._handle = handle
        self._write_owner()

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            self._unlock(handle)
        finally:
            handle.close()

    def __enter__(self) -> RuntimeInstanceLock:
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()

    def _lock(self, handle: IO[bytes]) -> None:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(self, handle: IO[bytes]) -> None:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _write_owner(self) -> None:
        handle = self._handle
        if handle is None or os.name == "nt":
            return
        payload = f"pid={os.getpid()} owner={self.owner}\n".encode()
        handle.seek(0)
        handle.truncate()
        handle.write(payload)
        handle.flush()
