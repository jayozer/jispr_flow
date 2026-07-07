"""Frontmost-app adapters: mock provider, platform factory dispatch, best-effort backends."""

import ctypes
import subprocess
import sys
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

import local_flow.context.frontmost as frontmost
from local_flow.context import (
    AppInfo,
    FrontmostAppProvider,
    MockFrontmostApp,
    create_frontmost_provider,
)
from local_flow.context.frontmost import MacFrontmostApp, WindowsFrontmostApp, X11FrontmostApp


class TestAppInfo:
    def test_defaults_are_empty(self):
        info = AppInfo()
        assert info.app_id == ""
        assert info.title == ""

    def test_is_frozen(self):
        info = AppInfo("com.apple.Terminal", "Terminal")
        with pytest.raises(FrozenInstanceError):
            info.app_id = "other"


class TestMockFrontmostApp:
    def test_defaults_to_empty_app_info(self):
        assert MockFrontmostApp().current() == AppInfo()

    def test_returns_configured_info(self):
        info = AppInfo("com.tinyspeck.slackmacgap", "Slack")
        assert MockFrontmostApp(info).current() == info

    def test_info_is_settable_after_construction(self):
        mock = MockFrontmostApp()
        mock.info = AppInfo("com.apple.mail", "Mail")
        assert mock.current() == AppInfo("com.apple.mail", "Mail")


class TestFactory:
    def test_darwin_dispatches_to_mac(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")

        class FakeMac(FrontmostAppProvider):
            def current(self):
                return AppInfo()

        monkeypatch.setattr(frontmost, "MacFrontmostApp", FakeMac)
        assert isinstance(create_frontmost_provider(), FakeMac)

    def test_win32_dispatches_to_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")

        class FakeWindows(FrontmostAppProvider):
            def current(self):
                return AppInfo()

        monkeypatch.setattr(frontmost, "WindowsFrontmostApp", FakeWindows)
        assert isinstance(create_frontmost_provider(), FakeWindows)

    def test_linux_dispatches_to_x11(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")

        class FakeX11(FrontmostAppProvider):
            def current(self):
                return AppInfo()

        monkeypatch.setattr(frontmost, "X11FrontmostApp", FakeX11)
        assert isinstance(create_frontmost_provider(), FakeX11)

    def test_unknown_platform_falls_back_to_x11(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "freebsd13")

        class FakeX11(FrontmostAppProvider):
            def current(self):
                return AppInfo()

        monkeypatch.setattr(frontmost, "X11FrontmostApp", FakeX11)
        assert isinstance(create_frontmost_provider(), FakeX11)


class _FakeApp:
    def bundleIdentifier(self):
        return "com.apple.Terminal"

    def localizedName(self):
        return "Terminal"


class _FakeWorkspace:
    @staticmethod
    def sharedWorkspace():
        return _FakeWorkspace()

    def frontmostApplication(self):
        return _FakeApp()


class _FakeAppKit:
    NSWorkspace = _FakeWorkspace


class _NoAppWorkspace:
    @staticmethod
    def sharedWorkspace():
        return _NoAppWorkspace()

    def frontmostApplication(self):
        return None


class _NoAppAppKit:
    NSWorkspace = _NoAppWorkspace


class _BoomWorkspace:
    @staticmethod
    def sharedWorkspace():
        raise RuntimeError("AppKit boom")


class _BoomAppKit:
    NSWorkspace = _BoomWorkspace


class TestMacFrontmostApp:
    def test_maps_appkit_fields(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "AppKit", _FakeAppKit())
        assert MacFrontmostApp().current() == AppInfo("com.apple.Terminal", "Terminal")

    def test_missing_appkit_yields_empty(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "AppKit", None)  # simulates ImportError
        assert MacFrontmostApp().current() == AppInfo()

    def test_no_frontmost_application_yields_empty(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "AppKit", _NoAppAppKit())
        assert MacFrontmostApp().current() == AppInfo()

    def test_backend_raising_yields_empty(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "AppKit", _BoomAppKit())
        assert MacFrontmostApp().current() == AppInfo()


class _FakeUser32:
    def __init__(self, hwnd=777):
        self.hwnd = hwnd

    def GetForegroundWindow(self):
        return self.hwnd

    def GetWindowTextW(self, hwnd, buf, size):
        buf.value = "Untitled - Notepad"
        return len(buf.value)

    def GetWindowThreadProcessId(self, hwnd, pid_ref):
        pid_ref._obj.value = 4242
        return 1


class _FakeKernel32:
    def OpenProcess(self, access, inherit, pid):
        return 99

    def QueryFullProcessImageNameW(self, handle, flags, buf, size_ref):
        buf.value = r"C:\Program Files\Notepad++\notepad++.exe"
        return 1

    def CloseHandle(self, handle):
        return 1


class _FakeWindll:
    user32 = _FakeUser32()
    kernel32 = _FakeKernel32()


class _NoWindowUser32:
    def GetForegroundWindow(self):
        return 0


class _NoWindowWindll:
    user32 = _NoWindowUser32()
    kernel32 = None


class _BoomUser32:
    def GetForegroundWindow(self):
        raise OSError("WinAPI boom")


class _BoomWindll:
    user32 = _BoomUser32()
    kernel32 = None


class TestWindowsFrontmostApp:
    def test_off_windows_ctypes_has_no_windll(self):
        # ctypes.windll only exists on win32; accessing it elsewhere raises
        # AttributeError, which current() must swallow.
        assert not hasattr(ctypes, "windll")
        assert WindowsFrontmostApp().current() == AppInfo()

    def test_maps_fields_via_fake_windll(self, monkeypatch):
        monkeypatch.setattr(ctypes, "windll", _FakeWindll(), raising=False)
        info = WindowsFrontmostApp().current()
        assert info == AppInfo("notepad++.exe", "Untitled - Notepad")

    def test_no_foreground_window_yields_empty(self, monkeypatch):
        monkeypatch.setattr(ctypes, "windll", _NoWindowWindll(), raising=False)
        assert WindowsFrontmostApp().current() == AppInfo()

    def test_backend_raising_yields_empty(self, monkeypatch):
        monkeypatch.setattr(ctypes, "windll", _BoomWindll(), raising=False)
        assert WindowsFrontmostApp().current() == AppInfo()


def _xprop_stub(outputs):
    def fake_run(args, **kwargs):
        key = tuple(args[1:])
        return SimpleNamespace(returncode=0, stdout=outputs.get(key, ""))

    return fake_run


class TestX11FrontmostApp:
    def test_maps_wm_class_and_name(self, monkeypatch):
        outputs = {
            ("-root", "_NET_ACTIVE_WINDOW"): (
                "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x3a00007\n"
            ),
            ("-id", "0x3a00007", "WM_CLASS"): 'WM_CLASS(STRING) = "code", "Code"\n',
            ("-id", "0x3a00007", "_NET_WM_NAME"): (
                '_NET_WM_NAME(UTF8_STRING) = "main.py - Code"\n'
            ),
        }
        monkeypatch.setattr(frontmost.subprocess, "run", _xprop_stub(outputs))
        assert X11FrontmostApp().current() == AppInfo("Code", "main.py - Code")

    def test_no_active_window_yields_empty(self, monkeypatch):
        outputs = {("-root", "_NET_ACTIVE_WINDOW"): "_NET_ACTIVE_WINDOW:  not found.\n"}
        monkeypatch.setattr(frontmost.subprocess, "run", _xprop_stub(outputs))
        assert X11FrontmostApp().current() == AppInfo()

    def test_xprop_missing_yields_empty(self, monkeypatch):
        def fake_run(args, **kwargs):
            raise FileNotFoundError("no such file: xprop")

        monkeypatch.setattr(frontmost.subprocess, "run", fake_run)
        assert X11FrontmostApp().current() == AppInfo()

    def test_xprop_timeout_yields_empty(self, monkeypatch):
        def fake_run(args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="xprop", timeout=0.5)

        monkeypatch.setattr(frontmost.subprocess, "run", fake_run)
        assert X11FrontmostApp().current() == AppInfo()

    def test_nonzero_returncode_yields_empty(self, monkeypatch):
        def fake_run(args, **kwargs):
            return SimpleNamespace(returncode=1, stdout="")

        monkeypatch.setattr(frontmost.subprocess, "run", fake_run)
        assert X11FrontmostApp().current() == AppInfo()

    def test_timeout_budget_is_short(self):
        assert X11FrontmostApp._TIMEOUT <= 0.5
