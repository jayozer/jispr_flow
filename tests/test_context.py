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
    ContextRouter,
    FrontmostAppProvider,
    MockFrontmostApp,
    ResolvedContext,
    create_frontmost_provider,
)
from local_flow.context.frontmost import MacFrontmostApp, WindowsFrontmostApp, X11FrontmostApp
from local_flow.insertion.base import FakeTextSink
from local_flow.personalization.store import AppRule


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
    def __init__(self, app_id="com.apple.Terminal", title="Terminal"):
        self.app_id = app_id
        self.title = title

    def bundleIdentifier(self):
        return self.app_id

    def localizedName(self):
        return self.title


class _FakeProcessManager:
    def __init__(self):
        self.front_result = (0, (0, 1111))
        self.pid_result = (0, 111)
        self.calls = []
        self.ax_calls = []

    def GetFrontProcess(self, output):
        self.calls.append(("front", output))
        return self.front_result

    def GetProcessPID(self, serial_number, output):
        self.calls.append(("pid", serial_number, output))
        return self.pid_result

    def AXUIElementCreateSystemWide(self):
        self.ax_calls.append("system")
        raise AssertionError("Frontmost identity must not depend on AX permission")


class TestMacFrontmostApp:
    @pytest.fixture(autouse=True)
    def fake_backend(self, monkeypatch):
        self.process_manager = _FakeProcessManager()
        self.apps = {111: _FakeApp()}
        self.workspace_calls = []
        self.running_app_calls = []

        def stale_workspace():
            self.workspace_calls.append("workspace")
            return SimpleNamespace(frontmostApplication=lambda: _FakeApp("stale", "Stale"))

        def running_app(pid):
            self.running_app_calls.append(pid)
            return self.apps.get(pid)

        self.appkit = SimpleNamespace(
            NSRunningApplication=SimpleNamespace(
                runningApplicationWithProcessIdentifier_=running_app
            ),
            NSWorkspace=SimpleNamespace(sharedWorkspace=stale_workspace),
        )
        monkeypatch.setitem(sys.modules, "ApplicationServices", self.process_manager)
        monkeypatch.setitem(sys.modules, "AppKit", self.appkit)
        yield
        assert self.workspace_calls == []
        assert self.process_manager.ax_calls == []

    def test_maps_appkit_fields(self):
        assert MacFrontmostApp().current() == AppInfo("com.apple.Terminal", "Terminal")
        assert self.process_manager.calls == [
            ("front", None),
            ("pid", (0, 1111), None),
        ]
        assert self.running_app_calls == [111]

    def test_same_provider_follows_focus_changes_without_a_cocoa_run_loop(self):
        self.apps = {
            111: _FakeApp("com.apple.TextEdit", "TextEdit"),
            222: _FakeApp("com.apple.Safari", "Safari"),
        }
        provider = MacFrontmostApp()

        assert provider.current() == AppInfo("com.apple.TextEdit", "TextEdit")
        self.process_manager.front_result = (0, (0, 2222))
        self.process_manager.pid_result = (0, 222)
        assert provider.current() == AppInfo("com.apple.Safari", "Safari")
        self.process_manager.front_result = (0, (0, 1111))
        self.process_manager.pid_result = (0, 111)
        assert provider.current() == AppInfo("com.apple.TextEdit", "TextEdit")
        assert self.running_app_calls == [111, 222, 111]
        assert self.process_manager.calls == [
            ("front", None), ("pid", (0, 1111), None),
            ("front", None), ("pid", (0, 2222), None),
            ("front", None), ("pid", (0, 1111), None),
        ]

    def test_failure_after_success_never_returns_previous_app(self):
        provider = MacFrontmostApp()
        assert provider.current().app_id == "com.apple.Terminal"

        self.process_manager.front_result = (-600, None)

        assert provider.current() == AppInfo()
        assert self.running_app_calls == [111]

    def test_missing_appkit_yields_empty(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "AppKit", None)  # simulates ImportError
        assert MacFrontmostApp().current() == AppInfo()

    def test_missing_application_services_yields_empty(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "ApplicationServices", None)
        assert MacFrontmostApp().current() == AppInfo()

    @pytest.mark.parametrize("result", [(-600, None), (-50, (0, 1111)), (0, None)])
    def test_missing_or_failed_front_process_yields_empty(self, result):
        self.process_manager.front_result = result
        assert MacFrontmostApp().current() == AppInfo()
        assert self.running_app_calls == []
        assert self.process_manager.calls == [("front", None)]

    @pytest.mark.parametrize("pid", [None, 0, -1, True, "111", 111.0])
    def test_invalid_pid_yields_empty(self, pid):
        self.process_manager.pid_result = (0, pid)
        assert MacFrontmostApp().current() == AppInfo()
        assert self.running_app_calls == []

    def test_failed_pid_query_yields_empty_even_with_a_positive_pid(self):
        self.process_manager.pid_result = (-600, 111)
        assert MacFrontmostApp().current() == AppInfo()
        assert self.running_app_calls == []

    def test_missing_running_application_yields_empty(self):
        self.apps.clear()
        assert MacFrontmostApp().current() == AppInfo()

    def test_missing_app_metadata_is_empty(self):
        self.apps[111] = _FakeApp(None, None)
        assert MacFrontmostApp().current() == AppInfo()

    @pytest.mark.parametrize(
        "call",
        ["GetFrontProcess", "GetProcessPID"],
    )
    def test_process_manager_raising_yields_empty(self, monkeypatch, call):
        def fail(*_args):
            raise RuntimeError("Process Manager call failed")

        monkeypatch.setattr(self.process_manager, call, fail)
        assert MacFrontmostApp().current() == AppInfo()

    def test_running_application_lookup_raising_yields_empty(self, monkeypatch):
        def fail(*_args):
            raise RuntimeError("AppKit call failed")

        monkeypatch.setattr(
            self.appkit.NSRunningApplication, "runningApplicationWithProcessIdentifier_", fail
        )
        assert MacFrontmostApp().current() == AppInfo()

    def test_app_metadata_raising_yields_empty(self):
        class BadApp:
            def bundleIdentifier(self):
                raise RuntimeError("AppKit metadata failed")

        self.apps[111] = BadApp()
        assert MacFrontmostApp().current() == AppInfo()

    def test_localized_name_raising_yields_empty(self):
        class BadNameApp(_FakeApp):
            def localizedName(self):
                raise RuntimeError("AppKit name failed")

        self.apps[111] = BadNameApp()
        assert MacFrontmostApp().current() == AppInfo()

    @pytest.mark.parametrize("field", ["front_result", "pid_result"])
    @pytest.mark.parametrize("result", [None, (0,), (0, 111, "extra")])
    def test_malformed_process_result_yields_empty(self, field, result):
        setattr(self.process_manager, field, result)
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


class TestResolvedContext:
    def test_defaults_mean_use_pipeline_defaults(self):
        ctx = ResolvedContext()
        assert ctx.app_id == ""
        assert ctx.style is None
        assert ctx.sink is None


class TestContextRouter:
    def test_mapped_app_resolves_style_and_sink(self):
        provider = MockFrontmostApp(AppInfo("com.tinyspeck.slackmacgap", "Slack"))
        type_sink = FakeTextSink()
        rules = {"com.tinyspeck.slackmacgap": AppRule(style="casual", insert="type")}
        router = ContextRouter(provider, rules, {"type": type_sink})

        ctx = router.resolve()

        assert ctx.app_id == "com.tinyspeck.slackmacgap"
        assert ctx.style == "casual"
        assert ctx.sink is type_sink

    def test_unmapped_app_falls_back_to_pipeline_defaults(self):
        provider = MockFrontmostApp(AppInfo("com.apple.mail", "Mail"))
        rules = {"com.tinyspeck.slackmacgap": AppRule(style="casual")}
        router = ContextRouter(provider, rules, {})

        ctx = router.resolve()

        assert ctx.app_id == "com.apple.mail"
        assert ctx.style is None
        assert ctx.sink is None

    def test_no_rules_configured_falls_back_to_pipeline_defaults(self):
        provider = MockFrontmostApp(AppInfo("com.apple.mail", "Mail"))
        router = ContextRouter(provider, {}, {})

        ctx = router.resolve()

        assert ctx.app_id == "com.apple.mail"
        assert ctx.style is None
        assert ctx.sink is None

    def test_empty_rule_is_harmless(self):
        # A rule with neither style nor insert set (e.g. from `{"claude": {}}`)
        # must resolve to pipeline defaults, not blow up or set an empty string
        # style/sink lookup key.
        provider = MockFrontmostApp(AppInfo("claude", "Claude"))
        rules = {"claude": AppRule()}
        router = ContextRouter(provider, rules, {"type": FakeTextSink()})

        ctx = router.resolve()

        assert ctx.app_id == "claude"
        assert ctx.style is None
        assert ctx.sink is None

    def test_insert_method_not_in_sinks_map_falls_back_to_default_sink(self):
        provider = MockFrontmostApp(AppInfo("claude", "Claude Code"))
        rules = {"claude": AppRule(insert="type")}
        router = ContextRouter(provider, rules, {})  # no "type" sink registered

        ctx = router.resolve()

        assert ctx.sink is None

    def test_unknown_insert_override_warns_at_construction(self, capsys):
        # Review item 20: a typo'd per-app `insert` in app_styles.json used to
        # silently fall back to the default sink; it must warn (naming the
        # rule, the bad value, and the valid values) while still falling back.
        provider = MockFrontmostApp(AppInfo("com.tinyspeck.slackmacgap", "Slack"))
        rules = {"slack": AppRule(insert="Type")}  # case-sensitive miss
        ContextRouter(
            provider, rules, {"type": FakeTextSink(), "auto": FakeTextSink()}
        )

        err = capsys.readouterr().err
        assert "'Type'" in err  # the bad value
        assert "slack" in err  # which rule to fix
        assert "type" in err and "auto" in err  # the valid values

    def test_unknown_insert_override_still_falls_back_to_default_sink(self, capsys):
        provider = MockFrontmostApp(AppInfo("com.tinyspeck.slackmacgap", "Slack"))
        rules = {"slack": AppRule(style="casual", insert="Type")}
        router = ContextRouter(provider, rules, {"type": FakeTextSink()})

        ctx = router.resolve()

        assert ctx.sink is None  # default sink, same degradation as before
        assert ctx.style == "casual"  # the rest of the rule still applies

    def test_known_insert_override_does_not_warn(self, capsys):
        provider = MockFrontmostApp(AppInfo("com.tinyspeck.slackmacgap", "Slack"))
        rules = {"slack": AppRule(insert="type")}
        ContextRouter(provider, rules, {"type": FakeTextSink()})

        assert capsys.readouterr().err == ""

    def test_matches_against_title_when_app_id_unknown(self):
        provider = MockFrontmostApp(AppInfo("", "Claude Code"))
        rules = {"claude": AppRule(style="formal")}
        router = ContextRouter(provider, rules, {})

        ctx = router.resolve()

        assert ctx.style == "formal"
