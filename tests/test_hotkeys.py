"""Hotkey logic: shared press/release core, factory dispatch, space and fn machines."""

import sys
import threading

import pytest

from local_flow.config import load_config
from local_flow.errors import HotkeyBackendMissingError
from local_flow.hotkeys.base import (
    CallbackDispatcher,
    PushToTalkCore,
    TapListener,
    create_hotkey_listener,
    resolve_key,
)
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

    def test_no_handler_cancel_does_not_arm_suppression(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, None)
        core.key_down()
        core.cancel_down()
        core.key_down()  # auto-repeat: recording simply continues
        core.key_up()
        assert rec.events == ["press", "release"]


class TestPushToTalkCoreCancelGate:
    """The app-level `cancel_gate` lets a listener's cancel key discard a
    recording that a *different* listener started (e.g. Esc on the keyboard
    discarding a mouse-started recording) even though this instance's own
    `held` is False.
    """

    def test_gate_true_while_not_held_fires_cancel_without_touching_state(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel, cancel_gate=lambda: True)

        core.cancel_down()

        assert rec.events == ["cancel"]
        assert core.held is False
        assert core._suppressed is False

    def test_gate_false_while_not_held_is_a_noop(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel, cancel_gate=lambda: False)

        core.cancel_down()

        assert rec.events == []

    def test_no_gate_while_not_held_is_a_noop_same_as_before(self):
        rec = Recorder()
        core = PushToTalkCore(rec.press, rec.release, rec.cancel)

        core.cancel_down()

        assert rec.events == []

    def test_held_cancel_semantics_unchanged_regardless_of_gate(self):
        rec = Recorder()
        # Gate returns False: proves the held branch does not even consult
        # it -- held cancel takes priority, exactly like before this existed.
        core = PushToTalkCore(rec.press, rec.release, rec.cancel, cancel_gate=lambda: False)

        core.key_down()
        core.cancel_down()

        assert rec.events == ["press", "cancel"]
        assert core.held is False
        assert core._suppressed is True  # armed: swallows the pending physical release

        core.key_up()  # physical release afterwards: swallowed, no stop
        assert rec.events == ["press", "cancel"]

    def test_no_handler_with_gate_true_is_still_a_noop(self):
        core = PushToTalkCore(lambda: None, lambda: None, None, cancel_gate=lambda: True)

        core.cancel_down()  # must not raise; on_cancel is None


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

    def test_other_key_during_pending_flushes_the_space(self):
        # Fast rollover typing: "a<space>b" with the b down arriving before
        # the space up. The swallowed space must be replayed on the b press
        # (keeping "a b"), not re-ordered after it on the space release.
        m = SpaceStateMachine()
        m.space_down()
        flush = m.other_key_down()
        assert flush.replay_space and not flush.start and not flush.stop
        # The physical space release afterwards is swallowed: the space was
        # already typed by the flush.
        assert m.space_up() == SpaceActions()
        # A fresh press afterwards works again.
        assert m.space_down().start_timer

    def test_other_key_while_idle_or_recording_is_noop(self):
        m = SpaceStateMachine()
        assert m.other_key_down() == SpaceActions()
        m.space_down()
        m.hold_elapsed(m.generation)
        assert m.other_key_down() == SpaceActions()  # dictating: no flush

    def test_stale_timer_after_flush_does_not_start_recording(self):
        m = SpaceStateMachine()
        m.space_down()
        stale_gen = m.generation
        m.other_key_down()  # flushed; the hold timer is still in flight
        assert m.hold_elapsed(stale_gen) == SpaceActions()


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


class TestTapListener:
    """`TapListener` (Phase 6 E8, transform hotkey): fires `on_tap` on every
    press of one key, no hold/release/cancel semantics. `handle_press` is a
    plain instance method (not a closure built inside `run`), same pattern
    as `MousePushToTalk.handle_click` -- testable directly with sentinel key
    objects, no live pynput listener needed.
    """

    def test_press_of_target_key_fires_on_tap(self):
        calls = []
        listener = TapListener("f9")
        listener._on_tap = lambda: calls.append("tap")

        listener.handle_press(listener._target)

        assert calls == ["tap"]

    def test_press_of_other_key_is_ignored(self):
        calls = []
        listener = TapListener("f9")
        listener._on_tap = lambda: calls.append("tap")

        listener.handle_press(object())  # some other key, never equal to _target

        assert calls == []

    def test_repeated_press_while_held_fires_once(self):
        """OS auto-repeat sends a stream of press events with no release in
        between while a key is held down -- without the ``held`` guard this
        would fire the (slow, LLM-backed) transform repeatedly for what the
        user experiences as a single tap/hold. See ``TapListener``'s
        docstring.
        """
        calls = []
        listener = TapListener("f9")
        listener._on_tap = lambda: calls.append("tap")

        listener.handle_press(listener._target)
        listener.handle_press(listener._target)  # auto-repeat: still held
        listener.handle_press(listener._target)  # auto-repeat: still held

        assert calls == ["tap"]

    def test_press_release_press_fires_twice(self):
        """A real release re-arms the guard: a fresh press afterward fires
        again -- distinct taps are not suppressed, only same-hold repeats.
        """
        calls = []
        listener = TapListener("f9")
        listener._on_tap = lambda: calls.append("tap")

        listener.handle_press(listener._target)
        listener.handle_release(listener._target)
        listener.handle_press(listener._target)

        assert calls == ["tap", "tap"]

    def test_injected_release_does_not_rearm(self):
        """A synthetic release (e.g. this process's own typed output) must
        not re-arm the guard -- same ``injected`` invariant as every other
        hotkey listener.
        """
        calls = []
        listener = TapListener("f9")
        listener._on_tap = lambda: calls.append("tap")

        listener.handle_press(listener._target)
        listener.handle_release(listener._target, injected=True)
        listener.handle_press(listener._target)  # still "held": no-op

        assert calls == ["tap"]

    def test_release_of_other_key_does_not_rearm(self):
        calls = []
        listener = TapListener("f9")
        listener._on_tap = lambda: calls.append("tap")

        listener.handle_press(listener._target)
        listener.handle_release(object())  # some other key's release
        listener.handle_press(listener._target)  # still "held": no-op

        assert calls == ["tap"]

    def test_injected_events_are_ignored(self):
        calls = []
        listener = TapListener("f9")
        listener._on_tap = lambda: calls.append("tap")

        listener.handle_press(listener._target, injected=True)

        assert calls == []

    def test_no_on_tap_registered_yet_is_a_noop(self):
        # Before run() sets `_on_tap` (e.g. a stray event during construction):
        # must not raise.
        listener = TapListener("f9")
        listener.handle_press(listener._target)  # no exception

    def test_unknown_key_name_raises_with_hint(self):
        with pytest.raises(HotkeyBackendMissingError, match="Unknown hotkey"):
            TapListener("no_such_key")

    def test_run_wires_on_tap_and_drives_the_real_pynput_listener(self, monkeypatch):
        listener = TapListener("f9")
        monkeypatch.setattr(
            listener._keyboard, "Listener", lambda **kw: _FakeListenerContextManager()
        )
        calls = []

        listener.run(lambda: calls.append("tap"))

        # run() blocks on listener.join() via the fake context manager, then
        # returns; the important assertion is that `_on_tap` got wired so a
        # subsequent handle_press would actually fire it.
        listener.handle_press(listener._target)
        assert calls == ["tap"]


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

    def test_releases_completed_callback_while_waiting_for_next_task(self):
        dispatcher = CallbackDispatcher()
        ran = threading.Event()
        released = threading.Event()

        class Task:
            def __call__(self):
                ran.set()

            def __del__(self):
                released.set()

        task = Task()
        dispatcher.submit(task)
        del task

        assert ran.wait(timeout=1)
        assert released.wait(timeout=1)

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


class TestQuartzFnListenerConstruction:
    def test_refuses_non_darwin(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        from local_flow.hotkeys.macos_fn import QuartzFnListener

        with pytest.raises(HotkeyBackendMissingError, match="only be observed on macOS"):
            QuartzFnListener()

    def test_refuses_unsupported_cancel_key(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        from local_flow.hotkeys.macos_fn import QuartzFnListener

        with pytest.raises(HotkeyBackendMissingError, match="only supports 'esc'"):
            QuartzFnListener(cancel_key="f12")


class TestSpacePushToTalkConstruction:
    def test_refuses_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        from local_flow.hotkeys.space import SpacePushToTalk

        with pytest.raises(HotkeyBackendMissingError, match="suppression"):
            SpacePushToTalk()


class TestSpacePushToTalkCancelGate:
    """Space has no shared `PushToTalkCore` (its own state machine drives
    cancel), so the app-level gate is glued in directly by
    `_handle_press`'s cancel branch: it fires `on_cancel` when the space
    machine itself produced no cancel action AND the gate returns True,
    leaving the machine's own state untouched (the space key isn't
    involved). Exercises `_handle_press` directly -- no real OS listener is
    started.
    """

    def _listener(self, monkeypatch, **kwargs):
        monkeypatch.setattr(sys, "platform", "darwin")
        from local_flow.hotkeys.space import SpacePushToTalk

        return SpacePushToTalk(**kwargs)

    def test_gate_true_fires_cancel_and_leaves_machine_idle(self, monkeypatch):
        calls = []
        sp = self._listener(monkeypatch, cancel_gate=lambda: True)
        sp._on_cancel = lambda: calls.append("cancel")

        sp._handle_press(sp._keyboard.Key.esc)

        assert calls == ["cancel"]
        assert sp._machine.state == "idle"

    def test_gate_false_is_silent(self, monkeypatch):
        calls = []
        sp = self._listener(monkeypatch, cancel_gate=lambda: False)
        sp._on_cancel = lambda: calls.append("cancel")

        sp._handle_press(sp._keyboard.Key.esc)

        assert calls == []

    def test_no_gate_is_silent_same_as_before(self, monkeypatch):
        calls = []
        sp = self._listener(monkeypatch)
        sp._on_cancel = lambda: calls.append("cancel")

        sp._handle_press(sp._keyboard.Key.esc)

        assert calls == []

    def test_machine_cancel_fires_once_even_when_gate_is_also_true(self, monkeypatch):
        """No double-firing: when the space machine itself is mid-recording
        and produces a cancel action, the gate branch must not fire
        `on_cancel` a second time.
        """
        calls = []
        sp = self._listener(monkeypatch, cancel_gate=lambda: True)
        sp._on_cancel = lambda: calls.append("cancel")
        sp._machine.state = "recording"  # as if space were held and recording

        sp._handle_press(sp._keyboard.Key.esc)

        assert calls == ["cancel"]


class TestSpacePushToTalkRollover:
    """Fast rollover typing ("a<space>b" with the b down before the space
    up): `_handle_press` must flush the swallowed space on the next key's
    press so the replay keeps the typed order. Exercises `_handle_press`/
    `_handle_release` directly, like `TestSpacePushToTalkCancelGate` above --
    no real OS listener, and `_replay_space` is stubbed so nothing is typed.
    """

    def _listener(self, monkeypatch, replays, **kwargs):
        monkeypatch.setattr(sys, "platform", "darwin")
        from local_flow.hotkeys.space import SpacePushToTalk

        # A huge hold_ms keeps the real threading.Timer inert for the test.
        sp = SpacePushToTalk(hold_ms=60_000, **kwargs)
        monkeypatch.setattr(sp, "_replay_space", lambda: replays.append("space"))
        return sp

    def test_rollover_press_flushes_the_space_before_the_release(self, monkeypatch):
        replays = []
        sp = self._listener(monkeypatch, replays)

        sp._handle_press(sp._keyboard.Key.space)
        sp._handle_press(sp._keyboard.KeyCode.from_char("b"))
        assert replays == ["space"]  # flushed on the b press, not on space up

        sp._handle_release(sp._keyboard.Key.space)
        assert replays == ["space"]  # the physical release does not replay again

    def test_plain_tap_still_replays_on_release(self, monkeypatch):
        replays = []
        sp = self._listener(monkeypatch, replays)

        sp._handle_press(sp._keyboard.Key.space)
        sp._handle_release(sp._keyboard.Key.space)

        assert replays == ["space"]

    def test_injected_key_press_does_not_flush(self, monkeypatch):
        # A TypingSink-typed character (injected) while the space is pending
        # must not flush -- only a real key press means the user is typing.
        replays = []
        sp = self._listener(monkeypatch, replays)

        sp._handle_press(sp._keyboard.Key.space)
        sp._handle_press(sp._keyboard.KeyCode.from_char("b"), injected=True)
        assert replays == []

        sp._handle_release(sp._keyboard.Key.space)
        assert replays == ["space"]  # the normal tap replay still happens


def _config(**env):
    return load_config(env={f"LOCAL_FLOW_{k.upper()}": v for k, v in env.items()})


class TestFactory:
    def test_fn_rejected_off_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(HotkeyBackendMissingError, match="only be observed on macOS"):
            create_hotkey_listener(_config(hotkey="fn"))

    def test_space_rejected_on_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(HotkeyBackendMissingError, match="suppression"):
            create_hotkey_listener(_config(hotkey="space"))

    def test_fn_dispatches_to_quartz_listener_on_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        import local_flow.hotkeys.macos_fn as macos_fn

        created = {}

        class FakeFn:
            def __init__(self, cancel_key, cancel_gate=None):
                created["cancel_key"] = cancel_key
                created["cancel_gate"] = cancel_gate

        monkeypatch.setattr(macos_fn, "QuartzFnListener", FakeFn)
        gate = lambda: False  # noqa: E731
        listener = create_hotkey_listener(
            _config(hotkey="FN", cancel_hotkey="f12"), cancel_gate=gate
        )
        assert isinstance(listener, FakeFn)
        assert created["cancel_key"] == "f12"
        assert created["cancel_gate"] is gate

    def test_space_dispatches_to_space_listener_on_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        import local_flow.hotkeys.space as space_mod

        created = {}

        class FakeSpace:
            def __init__(self, hold_ms, cancel_key, cancel_gate=None):
                created.update(hold_ms=hold_ms, cancel_key=cancel_key, cancel_gate=cancel_gate)

        monkeypatch.setattr(space_mod, "SpacePushToTalk", FakeSpace)
        gate = lambda: True  # noqa: E731
        listener = create_hotkey_listener(
            _config(hotkey="space", hotkey_space_hold_ms="400"), cancel_gate=gate
        )
        assert isinstance(listener, FakeSpace)
        assert created == {"hold_ms": 400, "cancel_key": "esc", "cancel_gate": gate}

    def test_other_names_dispatch_to_pynput(self, monkeypatch):
        import local_flow.hotkeys.base as base_mod

        created = {}

        class FakePynput:
            def __init__(self, key_name, cancel_key="esc", cancel_gate=None):
                created.update(key_name=key_name, cancel_key=cancel_key, cancel_gate=cancel_gate)

        monkeypatch.setattr(base_mod, "PynputPushToTalk", FakePynput)
        listener = create_hotkey_listener(_config(hotkey="f9"))
        assert isinstance(listener, FakePynput)
        assert created["key_name"] == "f9"
        assert created["cancel_gate"] is None  # default call site: no gate passed

    def test_pynput_dispatch_threads_cancel_gate_through(self, monkeypatch):
        import local_flow.hotkeys.base as base_mod

        created = {}

        class FakePynput:
            def __init__(self, key_name, cancel_key="esc", cancel_gate=None):
                created["cancel_gate"] = cancel_gate

        monkeypatch.setattr(base_mod, "PynputPushToTalk", FakePynput)
        gate = lambda: True  # noqa: E731
        create_hotkey_listener(_config(hotkey="f9"), cancel_gate=gate)
        assert created["cancel_gate"] is gate


class _FakeListenerContextManager:
    """Stands in for `pynput.keyboard.Listener`'s context-manager protocol so
    `PynputPushToTalk.run` can be exercised without installing a real global
    hook (which needs OS permission and would block forever).
    """

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def join(self):
        return None

    def stop(self):
        return None


class TestPynputPushToTalkRunWiresCancelGate:
    """`run()` builds the shared `PushToTalkCore` itself (not `__init__`),
    so the stored `cancel_gate` must be threaded through there too.
    """

    def test_run_constructs_core_with_the_stored_cancel_gate(self, monkeypatch):
        import local_flow.hotkeys.base as base_mod

        captured = {}

        class FakeCore:
            def __init__(self, on_press, on_release, on_cancel, cancel_gate=None):
                captured["cancel_gate"] = cancel_gate

            def key_down(self):
                pass

            def key_up(self):
                pass

            def cancel_down(self):
                pass

        monkeypatch.setattr(base_mod, "PushToTalkCore", FakeCore)
        gate = lambda: True  # noqa: E731
        listener = base_mod.PynputPushToTalk("f9", cancel_gate=gate)
        monkeypatch.setattr(
            listener._keyboard, "Listener", lambda **kw: _FakeListenerContextManager()
        )

        listener.run(lambda: None, lambda: None, lambda: None)

        assert captured["cancel_gate"] is gate


class TestQuartzFnListenerRunWiresCancelGate:
    def test_run_constructs_core_with_the_stored_cancel_gate(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        import local_flow.hotkeys.macos_fn as macos_fn

        captured = {}

        class FakeCore:
            def __init__(self, on_press, on_release, on_cancel, cancel_gate=None):
                captured["cancel_gate"] = cancel_gate

            def key_down(self):
                captured.setdefault("events", []).append("press")

            def key_up(self):
                captured.setdefault("events", []).append("release")

            def cancel_down(self):
                captured.setdefault("events", []).append("cancel")

        monkeypatch.setattr(macos_fn, "PushToTalkCore", FakeCore)
        gate = lambda: True  # noqa: E731
        listener = macos_fn.QuartzFnListener(cancel_gate=gate)

        class FakeQuartz:
            kCGEventTapDisabledByTimeout = 1
            kCGEventTapDisabledByUserInput = 2
            kCGEventFlagsChanged = 3
            kCGEventKeyDown = 4
            kCGEventTapOptionDefault = 5
            kCGSessionEventTap = 6
            kCGHeadInsertEventTap = 7
            kCFRunLoopCommonModes = 8
            kCGKeyboardEventKeycode = 9
            kCGEventFlagMaskSecondaryFn = 1

            @staticmethod
            def CGEventMaskBit(_bit):
                return 0

            @staticmethod
            def CGEventTapCreate(*args, **_kwargs):
                captured["tap_args"] = args
                return object()

            @staticmethod
            def CGEventGetIntegerValueField(event, _field):
                return event["keycode"]

            @staticmethod
            def CGEventGetFlags(event):
                return event["flags"]

            @staticmethod
            def CFMachPortCreateRunLoopSource(*_a, **_k):
                return object()

            @staticmethod
            def CFRunLoopGetCurrent():
                return object()

            @staticmethod
            def CFRunLoopAddSource(*_a, **_k):
                return None

            @staticmethod
            def CGEventTapEnable(*_a, **_k):
                return None

            @staticmethod
            def CFRunLoopRun():
                return None  # fake: returns immediately instead of blocking forever

            @staticmethod
            def CFRunLoopStop(_run_loop):
                return None

            @staticmethod
            def CFRunLoopWakeUp(_run_loop):
                return None

        listener._quartz = FakeQuartz

        listener.run(lambda: None, lambda: None, lambda: None)

        assert captured["cancel_gate"] is gate
        assert captured["tap_args"][2] == FakeQuartz.kCGEventTapOptionDefault

        callback = captured["tap_args"][4]
        fn_down = {"keycode": 63, "flags": FakeQuartz.kCGEventFlagMaskSecondaryFn}
        fn_up = {"keycode": 63, "flags": 0}
        other_flags = {"keycode": 0, "flags": 0}

        # Fn press/release drive local-flow but are consumed so another
        # global Fn listener (including macOS Dictation) cannot duplicate the
        # utterance. Unrelated flagsChanged events still pass through.
        assert callback(None, FakeQuartz.kCGEventFlagsChanged, fn_down, None) is None
        assert callback(None, FakeQuartz.kCGEventFlagsChanged, fn_up, None) is None
        assert captured["events"] == ["press", "release"]
        assert (
            callback(None, FakeQuartz.kCGEventFlagsChanged, other_flags, None)
            is other_flags
        )
