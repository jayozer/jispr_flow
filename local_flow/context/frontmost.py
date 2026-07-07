"""Frontmost-app adapters: macOS AppKit, Windows ctypes, Linux xprop.

Context awareness is best-effort. Unlike the hotkey backends, nothing here
may ever raise out of ``current()``: a missing dependency, a denied OS call,
or a hung ``xprop`` all degrade to an empty :class:`AppInfo` so callers can
always ask "what app is this?" without a try/except of their own.

Platform-specific dependencies (AppKit, the Windows API) are imported lazily
inside ``current()`` so importing this module stays headless; stdlib modules
that are safe to import everywhere (``subprocess``, ``ctypes``) are imported
at module scope, matching ``local_flow.insertion.desktop``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass

_HEX_WINDOW_ID = re.compile(r"#\s*(0x[0-9a-fA-F]+)")
_QUOTED = re.compile(r'"([^"]*)"')


@dataclass(frozen=True)
class AppInfo:
    """Identity of the frontmost app/window; all-empty when unknown."""

    app_id: str = ""  # macOS bundle id, Windows exe basename lowercased, X11 WM_CLASS
    title: str = ""  # localized app/window name


class FrontmostAppProvider(ABC):
    """Reports the frontmost app. Best-effort: ``current()`` never raises."""

    @abstractmethod
    def current(self) -> AppInfo:
        """Return info about the frontmost app, or an empty AppInfo if unknown."""


class MockFrontmostApp(FrontmostAppProvider):
    """Test double: returns whatever ``.info`` is set to."""

    def __init__(self, info: AppInfo | None = None) -> None:
        self.info = info if info is not None else AppInfo()

    def current(self) -> AppInfo:
        return self.info


class MacFrontmostApp(FrontmostAppProvider):
    """Reads the frontmost app via AppKit's ``NSWorkspace``."""

    def current(self) -> AppInfo:
        try:
            import AppKit

            app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return AppInfo()
            return AppInfo(
                app_id=app.bundleIdentifier() or "",
                title=app.localizedName() or "",
            )
        except Exception:
            return AppInfo()


class WindowsFrontmostApp(FrontmostAppProvider):
    """Reads the foreground window's owning process and title via ctypes.

    No pywin32: ``GetForegroundWindow`` -> ``GetWindowThreadProcessId`` ->
    ``OpenProcess`` -> ``QueryFullProcessImageNameW`` gives the exe path;
    ``GetWindowTextW`` gives the title. Any missing window, protected
    process, or WinAPI failure degrades to an empty AppInfo.
    """

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def current(self) -> AppInfo:
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return AppInfo()

            title_buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title_buf, 512)
            title = title_buf.value or ""

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

            app_id = ""
            if pid.value:
                handle = kernel32.OpenProcess(
                    self._PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
                )
                if handle:
                    try:
                        size = wintypes.DWORD(1024)
                        path_buf = ctypes.create_unicode_buffer(1024)
                        ok = kernel32.QueryFullProcessImageNameW(
                            handle, 0, path_buf, ctypes.byref(size)
                        )
                        if ok:
                            app_id = path_buf.value.rsplit("\\", 1)[-1].lower()
                    finally:
                        kernel32.CloseHandle(handle)

            return AppInfo(app_id=app_id, title=title)
        except Exception:
            return AppInfo()


class X11FrontmostApp(FrontmostAppProvider):
    """Reads the active window's ``WM_CLASS``/title via ``xprop``.

    Every subprocess call gets a short timeout: a hung X server or a slow
    remote display must not block dictation.
    """

    _TIMEOUT = 0.5

    def current(self) -> AppInfo:
        try:
            win_id = self._active_window_id()
            if win_id is None:
                return AppInfo()
            return AppInfo(app_id=self._wm_class(win_id), title=self._wm_name(win_id))
        except Exception:
            return AppInfo()

    def _run(self, *args: str) -> str:
        result = subprocess.run(
            ["xprop", *args],
            capture_output=True,
            text=True,
            timeout=self._TIMEOUT,
            check=False,
        )
        return result.stdout if result.returncode == 0 else ""

    def _active_window_id(self) -> str | None:
        match = _HEX_WINDOW_ID.search(self._run("-root", "_NET_ACTIVE_WINDOW"))
        return match.group(1) if match else None

    def _wm_class(self, win_id: str) -> str:
        found = _QUOTED.findall(self._run("-id", win_id, "WM_CLASS"))
        return found[-1] if found else ""

    def _wm_name(self, win_id: str) -> str:
        found = _QUOTED.findall(self._run("-id", win_id, "_NET_WM_NAME"))
        return found[-1] if found else ""


def create_frontmost_provider() -> FrontmostAppProvider:
    """Build the platform frontmost-app provider for the current OS.

    Construction never touches OS-specific dependencies (those are deferred
    to ``current()``), so this never raises either.
    """
    if sys.platform == "darwin":
        return MacFrontmostApp()
    if sys.platform == "win32":
        return WindowsFrontmostApp()
    return X11FrontmostApp()
