"""Hotkey logic: shared press/release core, factory dispatch, space and fn machines."""

import threading

import pytest

from local_flow.errors import HotkeyBackendMissingError
from local_flow.hotkeys.base import CallbackDispatcher, PushToTalkCore, resolve_key
from local_flow.hotkeys.macos_fn import ESCAPE_KEYCODE, FnLogic
from local_flow.hotkeys.space import SpaceActions, SpaceStateMachine


class Recorder:
    def __init__(self):
        self.events = []

    def press(self):
        self.events.append("press")

    def release(self):
        self.events.append("release")

    def cancel(self):
        self.events.append("cancel")


class TestPushToTalkCore:
    def test_press_release_cycle(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel)
        core.key_down()
        core.key_up()
        assert rec.events == ["press", "release"]

    def test_auto_repeat_key_down_fires_press_once(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel)
        core.key_down()
        core.key_down()
        core.key_down()
        core.key_up()
        assert rec.events == ["press", "release"]

    def test_key_up_without_down_is_ignored(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel)
        core.key_up()
        assert rec.events == []

    def test_cancel_while_held_discards_and_swallows_release(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel)
        core.key_down()
        core.cancel_down()
        core.key_up()  # physical key released afterwards: no stop
        assert rec.events == ["press", "cancel"]

    def test_cancel_while_idle_is_ignored(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel)
        core.cancel_down()
        assert rec.events == []

    def test_cancel_without_handler_keeps_recording(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, None)
        core.key_down()
        core.cancel_down()
        core.key_up()
        assert rec.events == ["press", "release"]

    def test_auto_repeat_after_cancel_does_not_restart(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel)
        core.key_down()
        core.cancel_down()
        core.key_down()  # OS auto-repeat: the key is still physically held
        core.key_up()
        core.key_down()  # a fresh press afterwards works again
        assert rec.events == ["press", "cancel", "press"]


class TestSpaceStateMachine:
    def test_quick_tap_replays_a_space(self):
        m = SpaceStateMachine()
        down = m.space_down()
        assert down.start_timer and not down.start
        up = m.space_up()
        assert up.replay_space and not up.stop

    def test_hold_starts_then_release_stops(self):
        m = SpaceStateMachine()
        m.space_down()
        held = m.hold_elapsed(m.generation)
        assert held.start
        up = m.space_up()
        assert up.stop and not up.replay_space

    def test_stale_timer_after_tap_does_not_start(self):
        m = SpaceStateMachine()
        m.space_down()
        stale_gen = m.generation
        m.space_up()  # tap finished; timer not yet cancelled
        late = m.hold_elapsed(stale_gen)
        assert late == SpaceActions()  # no-op

    def test_auto_repeat_downs_are_ignored(self):
        m = SpaceStateMachine()
        m.space_down()
        assert m.space_down() == SpaceActions()  # repeat while pending
        m.hold_elapsed(m.generation)
        assert m.space_down() == SpaceActions()  # repeat while recording

    def test_cancel_while_recording_discards(self):
        m = SpaceStateMachine()
        m.space_down()
        m.hold_elapsed(m.generation)
        assert m.cancel_down().cancel
        assert m.space_down() == SpaceActions()  # auto-repeat while still held: no restart
        assert m.space_up() == SpaceActions()  # physical release: swallowed, no stop
        assert m.space_down().start_timer  # a fresh press afterwards works again

    def test_cancel_while_idle_or_pending_is_noop(self):
        m = SpaceStateMachine()
        assert m.cancel_down() == SpaceActions()
        m.space_down()
        assert m.cancel_down() == SpaceActions()

    def test_up_while_idle_is_noop(self):
        assert SpaceStateMachine().space_up() == SpaceActions()


class FakeKeyCode:
    @staticmethod
    def from_char(char):
        return ("char", char)


class FakeKeys:
    esc = "ESC"
    f9 = "F9"
    space = "SPACE"


class FakeKeyboard:
    Key = FakeKeys
    KeyCode = FakeKeyCode


class TestResolveKey:
    def test_special_name_resolves_via_key_enum(self):
        assert resolve_key(FakeKeyboard, "esc") == "ESC"
        assert resolve_key(FakeKeyboard, "F9") == "F9"

    def test_single_character_resolves_via_keycode(self):
        assert resolve_key(FakeKeyboard, "x") == ("char", "x")

    def test_unknown_name_raises_with_hint(self):
        with pytest.raises(HotkeyBackendMissingError, match="Unknown hotkey"):
            resolve_key(FakeKeyboard, "no_such_key")


class TestCallbackDispatcher:
    def test_runs_callbacks_in_order_off_the_calling_thread(self):
        dispatcher = CallbackDispatcher()
        results = []
        done = threading.Event()

        def first():
            results.append(("first", threading.current_thread()))

        def second():
            results.append(("second", threading.current_thread()))
            done.set()

        dispatcher.wrap(first)()
        dispatcher.wrap(second)()
        assert done.wait(timeout=2)
        assert [name for name, _ in results] == ["first", "second"]
        assert all(t is not threading.main_thread() for _, t in results)

    def test_worker_survives_a_failing_callback(self, capsys):
        dispatcher = CallbackDispatcher()
        done = threading.Event()

        def boom():
            raise RuntimeError("kaput")

        dispatcher.wrap(boom)()
        dispatcher.wrap(done.set)()
        assert done.wait(timeout=2)
        assert "kaput" in capsys.readouterr().err

    def test_wrap_none_is_none(self):
        assert CallbackDispatcher().wrap(None) is None


class TestFnLogic:
    def _logic(self, rec, cancel=ESCAPE_KEYCODE):
        return FnLogic(PushToTalkCore(rec.press, rec.release, rec.cancel), cancel)

    def test_fn_press_release(self):
        rec = Recorder()
        logic = self._logic(rec)
        logic.flags_changed(True)
        logic.flags_changed(False)
        assert rec.events == ["press", "release"]

    def test_repeated_flag_states_do_not_repeat_callbacks(self):
        rec = Recorder()
        logic = self._logic(rec)
        logic.flags_changed(True)
        logic.flags_changed(True)  # e.g. fn+arrow re-reports the same mask
        logic.flags_changed(False)
        logic.flags_changed(False)
        assert rec.events == ["press", "release"]

    def test_escape_while_held_cancels(self):
        rec = Recorder()
        logic = self._logic(rec)
        logic.flags_changed(True)
        logic.key_down(ESCAPE_KEYCODE)
        logic.flags_changed(False)
        assert rec.events == ["press", "cancel"]

    def test_other_keys_ignored(self):
        rec = Recorder()
        logic = self._logic(rec)
        logic.flags_changed(True)
        logic.key_down(0)  # kVK_ANSI_A
        logic.flags_changed(False)
        assert rec.events == ["press", "release"]

    def test_no_cancel_keycode_ignores_escape(self):
        rec = Recorder()
        logic = self._logic(rec, cancel=None)
        logic.flags_changed(True)
        logic.key_down(ESCAPE_KEYCODE)
        logic.flags_changed(False)
        assert rec.events == ["press", "release"]
