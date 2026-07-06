"""Hotkey logic: shared press/release core, factory dispatch, space and fn machines."""

import pytest

from local_flow.errors import HotkeyBackendMissingError
from local_flow.hotkeys.base import PushToTalkCore, resolve_key
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
