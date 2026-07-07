"""Tests for the pure/headless parts of `local_flow.tray.app`.

`TrayApp` itself builds a real pipeline (audio source, ASR, LM Studio
client) and a real pystray `Icon`, so it is manual-verify only (see the
README's "Tray app" section) -- these tests cover everything that doesn't
require constructing one: `parse_languages`, the `MenuEntry`/`build_menu`
pure menu structure, `TrayReporter` driven with a fake icon recorder, and
the platform dispatch in `_open_folder`.
"""

from __future__ import annotations

import threading

import pytest

from local_flow.tray.app import (
    MenuEntry,
    TrayApp,
    TrayReporter,
    _open_folder,
    _to_pystray_menu,
    build_menu,
    parse_languages,
)
from local_flow.tray.state import TrayStateMachine


class TestParseLanguages:
    def test_splits_and_strips_whitespace(self):
        assert parse_languages("en, de,tr") == ["en", "de", "tr"]

    def test_empty_string_yields_empty_list(self):
        assert parse_languages("") == []

    def test_whitespace_only_yields_empty_list(self):
        assert parse_languages("   ") == []

    def test_blank_entries_are_dropped(self):
        assert parse_languages("en,,de,") == ["en", "de"]

    def test_duplicates_are_removed_preserving_first_seen_order(self):
        assert parse_languages("en, de, en, fr, de") == ["en", "de", "fr"]

    def test_single_code_no_commas(self):
        assert parse_languages("en") == ["en"]


class TestBuildMenuHandsFree:
    def test_dictation_entry_shows_start_when_not_running(self):
        entries = build_menu(
            mode="hands-free",
            running=False,
            toggle_running=lambda: None,
            style_names=[],
            current_style="default",
            set_style=lambda name: None,
            languages=[],
            current_language=None,
            set_language=lambda code: None,
            open_data_folder=lambda: None,
            quit_app=lambda: None,
        )
        assert entries[0].label == "Start dictation"
        assert entries[0].enabled is True
        assert entries[0].action is not None

    def test_dictation_entry_shows_stop_when_running(self):
        entries = build_menu(
            mode="hands-free",
            running=True,
            toggle_running=lambda: None,
            style_names=[],
            current_style="default",
            set_style=lambda name: None,
            languages=[],
            current_language=None,
            set_language=lambda code: None,
            open_data_folder=lambda: None,
            quit_app=lambda: None,
        )
        assert entries[0].label == "Stop dictation"

    def test_toggle_running_action_is_wired(self):
        calls = []
        entries = build_menu(
            mode="hands-free",
            running=False,
            toggle_running=lambda: calls.append("toggled"),
            style_names=[],
            current_style="default",
            set_style=lambda name: None,
            languages=[],
            current_language=None,
            set_language=lambda code: None,
            open_data_folder=lambda: None,
            quit_app=lambda: None,
        )
        entries[0].action()
        assert calls == ["toggled"]


class TestBuildMenuPushToTalk:
    def test_dictation_entry_is_a_disabled_status_label(self):
        entries = build_menu(
            mode="push-to-talk",
            running=False,
            toggle_running=lambda: None,
            style_names=[],
            current_style="default",
            set_style=lambda name: None,
            languages=[],
            current_language=None,
            set_language=lambda code: None,
            open_data_folder=lambda: None,
            quit_app=lambda: None,
        )
        assert entries[0].label == "Dictation: listening for hotkey"
        assert entries[0].enabled is False
        assert entries[0].action is None


class TestBuildMenuModeLabel:
    def test_mode_entry_is_disabled_and_shows_mode(self):
        entries = build_menu(
            mode="push-to-talk",
            running=False,
            toggle_running=lambda: None,
            style_names=[],
            current_style="default",
            set_style=lambda name: None,
            languages=[],
            current_language=None,
            set_language=lambda code: None,
            open_data_folder=lambda: None,
            quit_app=lambda: None,
        )
        mode_entry = entries[1]
        assert mode_entry.label == "Mode: push-to-talk"
        assert mode_entry.enabled is False


class TestBuildMenuStyleSubmenu:
    def _entries(self, style_names, current_style, set_style=lambda name: None):
        return build_menu(
            mode="push-to-talk",
            running=False,
            toggle_running=lambda: None,
            style_names=style_names,
            current_style=current_style,
            set_style=set_style,
            languages=[],
            current_language=None,
            set_language=lambda code: None,
            open_data_folder=lambda: None,
            quit_app=lambda: None,
        )

    def test_no_style_names_hides_the_submenu(self):
        entries = self._entries([], "default")
        assert not any(e.label == "Style" for e in entries)

    def test_style_submenu_lists_all_names(self):
        entries = self._entries(["default", "casual", "email"], "default")
        style_menu = next(e for e in entries if e.label == "Style")
        assert [sub.label for sub in style_menu.submenu] == ["default", "casual", "email"]

    def test_current_style_is_checked(self):
        entries = self._entries(["default", "casual"], "casual")
        style_menu = next(e for e in entries if e.label == "Style")
        checked = {sub.label: sub.checked for sub in style_menu.submenu}
        assert checked == {"default": False, "casual": True}

    def test_clicking_a_style_calls_set_style_with_its_name(self):
        calls = []
        entries = self._entries(
            ["default", "casual"], "default", set_style=lambda name: calls.append(name)
        )
        style_menu = next(e for e in entries if e.label == "Style")
        casual_item = next(sub for sub in style_menu.submenu if sub.label == "casual")
        casual_item.action()
        assert calls == ["casual"]


class TestBuildMenuLanguageSubmenu:
    def _entries(self, languages, current_language, set_language=lambda code: None):
        return build_menu(
            mode="push-to-talk",
            running=False,
            toggle_running=lambda: None,
            style_names=[],
            current_style="default",
            set_style=lambda name: None,
            languages=languages,
            current_language=current_language,
            set_language=set_language,
            open_data_folder=lambda: None,
            quit_app=lambda: None,
        )

    def test_no_languages_hides_the_submenu(self):
        entries = self._entries([], None)
        assert not any(e.label == "Language" for e in entries)

    def test_language_submenu_lists_all_codes(self):
        entries = self._entries(["en", "de", "tr"], "en")
        language_menu = next(e for e in entries if e.label == "Language")
        assert [sub.label for sub in language_menu.submenu] == ["en", "de", "tr"]

    def test_current_language_is_checked(self):
        entries = self._entries(["en", "de"], "de")
        language_menu = next(e for e in entries if e.label == "Language")
        checked = {sub.label: sub.checked for sub in language_menu.submenu}
        assert checked == {"en": False, "de": True}

    def test_clicking_a_language_calls_set_language_with_its_code(self):
        calls = []
        entries = self._entries(
            ["en", "de"], "en", set_language=lambda code: calls.append(code)
        )
        language_menu = next(e for e in entries if e.label == "Language")
        de_item = next(sub for sub in language_menu.submenu if sub.label == "de")
        de_item.action()
        assert calls == ["de"]


class TestBuildMenuTrailingItems:
    def test_open_data_folder_and_quit_are_always_present_and_last(self):
        entries = build_menu(
            mode="push-to-talk",
            running=False,
            toggle_running=lambda: None,
            style_names=["default"],
            current_style="default",
            set_style=lambda name: None,
            languages=["en"],
            current_language="en",
            set_language=lambda code: None,
            open_data_folder=lambda: None,
            quit_app=lambda: None,
        )
        assert [e.label for e in entries[-2:]] == ["Open data folder", "Quit"]

    def test_quit_action_is_wired(self):
        calls = []
        entries = build_menu(
            mode="push-to-talk",
            running=False,
            toggle_running=lambda: None,
            style_names=[],
            current_style="default",
            set_style=lambda name: None,
            languages=[],
            current_language=None,
            set_language=lambda code: None,
            open_data_folder=lambda: None,
            quit_app=lambda: calls.append("quit"),
        )
        entries[-1].action()
        assert calls == ["quit"]


class FakeIcon:
    """Minimal stand-in for a `pystray.Icon`: records `.icon`/`.title`/`.notify(...)`."""

    def __init__(self) -> None:
        self.icon = None
        self.title = None
        self.notifications: list[str] = []

    def notify(self, detail: str) -> None:
        self.notifications.append(detail)


class TestTrayReporter:
    """`TrayReporter` applies state transitions to any icon-shaped object,
    so it's testable with a fake recorder -- no pystray needed. `draw_icon`
    (called internally) needs Pillow, so these are guarded like Task 2's
    icon tests.
    """

    @pytest.fixture(autouse=True)
    def _require_pillow(self):
        pytest.importorskip("PIL")

    def test_recording_sets_the_recording_icon_and_tooltip(self):
        icon = FakeIcon()
        reporter = TrayReporter(icon, TrayStateMachine())

        reporter.notify("recording")

        assert icon.icon is not None
        assert icon.title == "local-flow — recording"

    def test_inserted_reverts_to_idle_icon_with_a_flash_tooltip(self):
        icon = FakeIcon()
        reporter = TrayReporter(icon, TrayStateMachine())

        reporter.notify("inserted", "send the invoice")

        assert icon.title == "inserted: send the invoice"

    def test_error_calls_icon_notify_with_the_detail(self):
        icon = FakeIcon()
        reporter = TrayReporter(icon, TrayStateMachine())

        reporter.notify("error", "LM Studio unreachable")

        assert icon.notifications == ["LM Studio unreachable"]

    def test_warning_calls_icon_notify_with_the_detail(self):
        icon = FakeIcon()
        reporter = TrayReporter(icon, TrayStateMachine())

        reporter.notify("warning", "LM Studio polish skipped")

        assert icon.notifications == ["LM Studio polish skipped"]

    def test_recording_does_not_trigger_a_notification(self):
        icon = FakeIcon()
        reporter = TrayReporter(icon, TrayStateMachine())

        reporter.notify("recording")

        assert icon.notifications == []

    def test_default_state_machine_is_constructed_when_omitted(self):
        icon = FakeIcon()
        reporter = TrayReporter(icon)

        reporter.notify("processing")

        assert icon.title == "local-flow — processing"

    def test_icon_update_failure_is_swallowed(self):
        class BoomIcon:
            @property
            def icon(self):
                return None

            @icon.setter
            def icon(self, value):
                raise RuntimeError("no display")

            def notify(self, detail):
                raise RuntimeError("no notifications backend")

        reporter = TrayReporter(BoomIcon(), TrayStateMachine())

        reporter.notify("error", "boom")  # must not raise


class TestOpenFolder:
    def test_darwin_uses_open(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr(
            "subprocess.run", lambda cmd, **kwargs: calls.append(cmd)
        )
        _open_folder(tmp_path)
        assert calls == [["open", str(tmp_path)]]

    def test_windows_uses_explorer(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr(
            "subprocess.run", lambda cmd, **kwargs: calls.append(cmd)
        )
        _open_folder(tmp_path)
        assert calls == [["explorer", str(tmp_path)]]

    def test_linux_uses_xdg_open(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr(
            "subprocess.run", lambda cmd, **kwargs: calls.append(cmd)
        )
        _open_folder(tmp_path)
        assert calls == [["xdg-open", str(tmp_path)]]

    def test_missing_binary_is_swallowed(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sys.platform", "linux")

        def boom(cmd, **kwargs):
            raise OSError("not found")

        monkeypatch.setattr("subprocess.run", boom)
        _open_folder(tmp_path)  # must not raise


class TestStartLoopGuardsAgainstDoubleStart:
    """`_start_loop` must never let two `_run_loop` threads run concurrently
    (a carry-over review fix: Stop-then-Start, clicked before the old thread
    notices its stop event and winds down, used to spawn a second thread
    capturing the same mic -- double capture / PortAudio device-busy).

    `TrayApp.__init__` requires the `pystray`/`Pillow` extras and builds a
    real pipeline (audio source, ASR, LM Studio client), so it can't be
    constructed headlessly here. Instead we bypass `__init__` via
    `object.__new__` and set only the handful of attributes `_start_loop`/
    `_stop_loop` touch, then monkeypatch `local_flow.app._run_loop` (which
    `_start_loop` imports fresh on every call, so patching it before the
    call takes effect) with a fake so no real audio/ASR/LLM is exercised.
    `local_flow.tray.app._JOIN_TIMEOUT` is monkeypatched down so the
    "still-alive" case doesn't slow the suite down.
    """

    def _bare_app(self, mode: str = "hands-free") -> TrayApp:
        app = object.__new__(TrayApp)
        app.config = None
        app.mode = mode
        app.reporter = None
        app.pipeline = app.source = app.vad = None
        app._running = False
        app._stop_event = threading.Event()
        app._loop_thread = None
        return app

    def test_second_start_is_a_noop_while_previous_thread_is_still_alive(
        self, monkeypatch
    ):
        monkeypatch.setattr("local_flow.tray.app._JOIN_TIMEOUT", 0.05)
        app = self._bare_app()
        still_running = threading.Event()

        def fake_run_loop(config, mode, reporter, stop_event, dependencies):
            # Ignores `stop_event`, simulating a thread that hasn't noticed
            # a stop request yet (the exact window this guard protects).
            still_running.wait(timeout=5)

        monkeypatch.setattr("local_flow.app._run_loop", fake_run_loop)

        app._start_loop()
        first_thread = app._loop_thread
        assert first_thread.is_alive()

        app._running = False  # as `_stop_loop` would have set, mid-stop
        app._start_loop()

        assert app._loop_thread is first_thread, "must not spawn a second loop thread"

        still_running.set()
        first_thread.join(timeout=2)

    def test_start_spawns_a_fresh_thread_once_the_previous_one_has_exited(
        self, monkeypatch
    ):
        monkeypatch.setattr("local_flow.tray.app._JOIN_TIMEOUT", 2.0)
        app = self._bare_app()
        run_count = []

        def fake_run_loop(config, mode, reporter, stop_event, dependencies):
            run_count.append(1)
            stop_event.wait(timeout=2)  # exits promptly once told to stop

        monkeypatch.setattr("local_flow.app._run_loop", fake_run_loop)

        app._start_loop()
        first_thread = app._loop_thread
        app._running = False
        app._stop_event.set()
        first_thread.join(timeout=2)
        assert not first_thread.is_alive()

        app._start_loop()

        assert app._loop_thread is not first_thread
        assert len(run_count) == 2
        app._loop_thread.is_alive()
        app._stop_event.set()
        app._loop_thread.join(timeout=2)

    def test_start_is_a_noop_when_already_marked_running(self, monkeypatch):
        app = self._bare_app()
        app._running = True
        app._loop_thread = None  # no thread object needed for this branch

        def fake_run_loop(*args, **kwargs):
            raise AssertionError("_run_loop must not be started again")

        monkeypatch.setattr("local_flow.app._run_loop", fake_run_loop)

        app._start_loop()  # must return immediately without starting anything


class TestPystrayMenuConversion:
    """`_to_pystray_menu` converts the pure `MenuEntry` tree into a real
    `pystray.Menu`; guarded so the suite still passes without the ``tray``
    extra installed.
    """

    @pytest.fixture(autouse=True)
    def _require_pystray(self):
        pytest.importorskip("pystray")

    def test_flat_entries_convert_one_to_one(self):
        entries = (
            MenuEntry(label="Quit", action=lambda: None),
            MenuEntry(label="Disabled", action=None, enabled=False),
        )
        menu = _to_pystray_menu(entries)
        assert [item.text for item in menu.items] == ["Quit", "Disabled"]
        assert menu.items[1].enabled is False

    def test_submenu_entries_become_nested_menus(self):
        entries = (
            MenuEntry(
                label="Style",
                submenu=(
                    MenuEntry(label="default", action=lambda: None, checked=True),
                    MenuEntry(label="casual", action=lambda: None, checked=False),
                ),
            ),
        )
        menu = _to_pystray_menu(entries)
        style_item = menu.items[0]
        assert style_item.text == "Style"
        sub_labels = [sub.text for sub in style_item.submenu.items]
        assert sub_labels == ["default", "casual"]
        assert style_item.submenu.items[0].checked is True
        assert style_item.submenu.items[1].checked is False

    def test_action_callback_fires_through_pystray_item_call(self):
        calls = []
        entries = (MenuEntry(label="Quit", action=lambda: calls.append("quit")),)
        menu = _to_pystray_menu(entries)
        menu.items[0](icon=None)
        assert calls == ["quit"]
