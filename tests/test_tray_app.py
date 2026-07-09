"""Tests for the pure/headless parts of `local_flow.tray.app`.

`TrayApp` itself builds a real pipeline (audio source, ASR, LM Studio
client) and a real pystray `Icon`, so it is manual-verify only (see the
README's "Tray app" section) -- these tests cover everything that doesn't
require constructing one: `parse_languages`, the `MenuEntry`/`build_menu`
pure menu structure, `TrayReporter` driven with a fake icon recorder, and
the platform dispatch in `_open_folder`.
"""

from __future__ import annotations

import queue
import threading

import pytest

from local_flow.status import State, StatusReporter
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
    """Fake tray backend standing in for a `pystray.Icon`.

    Beyond echoing `.icon`/`.title` back like the real thing, it records how
    many times each was assigned and on WHICH thread -- the two facts the
    review-item-15 tests assert on (updates marshaled to the main thread,
    redraws skipped when the icon kind is unchanged).
    """

    def __init__(self) -> None:
        self._icon = None
        self._title = None
        self.icon_update_count = 0
        self.icon_update_threads: list[threading.Thread] = []
        self.title_update_threads: list[threading.Thread] = []
        self.notifications: list[str] = []

    @property
    def icon(self):
        return self._icon

    @icon.setter
    def icon(self, value) -> None:
        self._icon = value
        self.icon_update_count += 1
        self.icon_update_threads.append(threading.current_thread())

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, value) -> None:
        self._title = value
        self.title_update_threads.append(threading.current_thread())

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


class TestTrayReporterMarshalsToMainThread:
    """Review item 15: pystray's darwin backend drives AppKit when `.icon`/
    `.title` are assigned, and AppKit is only safe to touch from the main
    thread -- but `TrayReporter.notify` fires on the dictation-loop thread.
    `notify` must therefore route EVERY icon mutation through its dispatch
    seam instead of mutating inline; the fake backend records which thread
    each update was applied on.
    """

    @pytest.fixture(autouse=True)
    def _require_pillow(self):
        pytest.importorskip("PIL")

    def test_notify_from_a_worker_thread_defers_all_updates_to_the_dispatcher(self):
        icon = FakeIcon()
        pending: queue.Queue = queue.Queue()
        reporter = TrayReporter(icon, TrayStateMachine(), dispatch=pending.put)

        worker = threading.Thread(target=lambda: reporter.notify("recording"))
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()

        # Nothing may have touched the icon from the worker thread...
        assert icon.icon_update_count == 0
        assert icon.title is None

        # ...the queued closure applies everything on whichever thread runs
        # the dispatcher -- here, the main thread.
        while not pending.empty():
            pending.get()()
        assert icon.title == "local-flow — recording"
        assert icon.icon_update_threads == [threading.main_thread()]
        assert icon.title_update_threads == [threading.main_thread()]

    def test_error_desktop_notification_is_also_dispatched_not_inline(self):
        icon = FakeIcon()
        pending: queue.Queue = queue.Queue()
        reporter = TrayReporter(icon, TrayStateMachine(), dispatch=pending.put)

        worker = threading.Thread(
            target=lambda: reporter.notify("error", "LM Studio unreachable")
        )
        worker.start()
        worker.join(timeout=2)

        assert icon.notifications == []
        while not pending.empty():
            pending.get()()
        assert icon.notifications == ["LM Studio unreachable"]

    def test_default_dispatch_applies_inline_on_the_main_thread(self):
        # Without an injected dispatcher, a notify already on the main
        # thread must apply synchronously (no run loop to queue onto).
        icon = FakeIcon()
        reporter = TrayReporter(icon, TrayStateMachine())

        reporter.notify("recording")

        assert icon.title == "local-flow — recording"
        assert icon.icon_update_threads == [threading.main_thread()]


class TestTrayReporterSkipsUnchangedRedraws:
    """Review item 15 (second half): re-rendering and re-assigning the icon
    image is the expensive part (a Pillow draw plus a native redraw), so
    `notify` must skip it when the mapped icon KIND is unchanged -- the
    tooltip still updates. Streaming previews are the hot path: every
    partial transcript maps to the same "processing" kind.
    """

    @pytest.fixture(autouse=True)
    def _require_pillow(self):
        pytest.importorskip("PIL")

    def test_same_icon_kind_updates_tooltip_without_redrawing(self):
        icon = FakeIcon()
        reporter = TrayReporter(icon, TrayStateMachine())

        reporter.notify("processing")
        reporter.notify("preview", "draft one")
        reporter.notify("preview", "draft one two")

        assert icon.icon_update_count == 1
        assert icon.title == "… draft one two"

    def test_each_icon_kind_change_redraws(self):
        icon = FakeIcon()
        reporter = TrayReporter(icon, TrayStateMachine())

        reporter.notify("recording")
        reporter.notify("processing")
        reporter.notify("inserted", "hi")  # maps to the idle icon (flash)

        assert icon.icon_update_count == 3

    def test_failed_redraw_is_retried_on_the_next_notify(self):
        # A swallowed redraw failure must not be recorded as "already
        # showing this kind", or the icon would stay stale forever once the
        # backend recovers.
        class FlakyIcon:
            def __init__(self) -> None:
                self.failures_left = 1
                self.applied: list[object] = []
                self.title = None

            @property
            def icon(self):
                return self.applied[-1] if self.applied else None

            @icon.setter
            def icon(self, value) -> None:
                if self.failures_left:
                    self.failures_left -= 1
                    raise RuntimeError("transient backend failure")
                self.applied.append(value)

            def notify(self, detail: str) -> None:
                pass

        flaky = FlakyIcon()
        reporter = TrayReporter(flaky, TrayStateMachine())

        reporter.notify("recording")  # redraw raises; swallowed
        reporter.notify("recording")  # same kind, but it never landed -> retry

        assert len(flaky.applied) == 1


def _bare_tray_app(mode: str = "hands-free") -> TrayApp:
    """A `TrayApp` skeleton without running `__init__` (which needs the
    `tray` extra and builds a real pipeline): only the attributes
    `_start_loop`/`_stop_loop` touch, via `object.__new__`."""
    app = object.__new__(TrayApp)
    app.config = None
    app.mode = mode
    app.reporter = None
    app.pipeline = app.source = app.vad = app.pending_store = None
    app._deps = None
    app._running = False
    app._stop_event = threading.Event()
    app._loop_thread = None
    return app


class RecordingReporter(StatusReporter):
    """Records every `(state, detail)` notify, in order."""

    def __init__(self) -> None:
        self.states: list[tuple[State, str]] = []

    def notify(self, state: State, detail: str = "") -> None:
        self.states.append((state, detail))


class TestStopLoopEmitsTerminalIdle:
    """Review item 27: the loop thread exits silently once it notices the
    stop event -- it never emits a final state, so stopping hands-free
    dictation left the tray icon stuck on red "recording". `_stop_loop`
    must emit a terminal "idle" so the tray's final state is idle.
    """

    def test_stop_emits_idle_as_the_terminal_state(self, monkeypatch):
        app = _bare_tray_app()
        reporter = RecordingReporter()
        app.reporter = reporter
        recording_seen = threading.Event()

        def fake_run_loop(config, mode, rep, stop_event, dependencies):
            rep.notify("recording")
            recording_seen.set()
            stop_event.wait(timeout=2)  # exits promptly once told to stop

        monkeypatch.setattr("local_flow.app._run_loop", fake_run_loop)
        app._start_loop()
        assert recording_seen.wait(timeout=2)

        app._stop_loop()

        assert reporter.states[-1] == ("idle", "")

    def test_stop_when_not_running_emits_nothing(self):
        app = _bare_tray_app()
        reporter = RecordingReporter()
        app.reporter = reporter

        app._stop_loop()  # early-returns: nothing was running

        assert reporter.states == []


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

    def test_second_start_is_a_noop_while_previous_thread_is_still_alive(
        self, monkeypatch
    ):
        monkeypatch.setattr("local_flow.tray.app._JOIN_TIMEOUT", 0.05)
        app = _bare_tray_app()
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
        app = _bare_tray_app()
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
        app = _bare_tray_app()
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
