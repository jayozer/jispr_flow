"""Mouse-button push-to-talk (E12): `MouseToggleMachine`, `resolve_mouse_button`,
`MousePushToTalk`'s handler methods, `create_mouse_listener`, config
validation for `mouse_button`/`mouse_mode`/`mouse_enter_button`, and
`_run_loop`'s push-to-talk wiring (mouse thread + keyboard listener sharing
callbacks, `mouse_enter_button` -> `sink.press_key("enter")`).
"""

import threading

import pytest

from local_flow.app import RunDependencies, _run_loop
from local_flow.asr.mock import MockTranscriber
from local_flow.config import load_config
from local_flow.errors import ConfigError, HotkeyBackendMissingError
from local_flow.hotkeys.base import PushToTalkCore, create_mouse_listener
from local_flow.hotkeys.mouse import (
    MousePushToTalk,
    MouseToggleMachine,
    resolve_mouse_button,
)
from local_flow.insertion.base import FakeTextSink
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.status import StatusReporter


def _config(**env):
    return load_config(env={f"LOCAL_FLOW_{k.upper()}": v for k, v in env.items()})


class Recorder:
    def __init__(self):
        self.events = []

    def press(self):
        self.events.append("press")

    def release(self):
        self.events.append("release")


class TestMouseToggleMachine:
    def test_alternates_start_then_stop(self):
        machine = MouseToggleMachine()
        assert machine.click() == "start"
        assert machine.click() == "stop"

    def test_keeps_alternating_across_many_clicks(self):
        machine = MouseToggleMachine()
        results = [machine.click() for _ in range(4)]
        assert results == ["start", "stop", "start", "stop"]


# A fake pynput.mouse-shaped module: only `Button.middle`/`Button.x1` exist,
# mirroring a platform (like macOS) whose backend doesn't expose `x2`.
class _FakeButton:
    middle = object()
    x1 = object()


class _FakeMouseModule:
    Button = _FakeButton


class TestResolveMouseButton:
    def test_middle_resolves_to_the_platform_button_object(self):
        assert resolve_mouse_button(_FakeMouseModule, "middle") is _FakeButton.middle

    def test_x1_resolves_when_the_platform_exposes_it(self):
        assert resolve_mouse_button(_FakeMouseModule, "x1") is _FakeButton.x1

    def test_left_is_rejected_as_unsupported(self):
        with pytest.raises(HotkeyBackendMissingError, match="Unsupported mouse button"):
            resolve_mouse_button(_FakeMouseModule, "left")

    def test_right_is_rejected_as_unsupported(self):
        with pytest.raises(HotkeyBackendMissingError, match="Unsupported mouse button"):
            resolve_mouse_button(_FakeMouseModule, "right")

    def test_platform_missing_the_button_raises_with_hint(self):
        with pytest.raises(HotkeyBackendMissingError, match="does not expose") as excinfo:
            resolve_mouse_button(_FakeMouseModule, "x2")
        assert "middle" in excinfo.value.hint


def _bare(mode: str, target=None, enter_target=None):
    """Build a `MousePushToTalk` bypassing `__init__` (which requires a real
    pynput import): sets exactly the attributes `handle_click` touches, using
    plain sentinel objects for the resolved button(s) -- proving the handler
    only ever compares button identity, never depends on real pynput types.
    """
    mpt = object.__new__(MousePushToTalk)
    mpt.button = "middle"
    mpt.mode = mode
    mpt.enter_button = "x1" if enter_target is not None else ""
    mpt._target = target
    mpt._enter_target = enter_target
    mpt.on_enter = None
    mpt._toggle = MouseToggleMachine()
    mpt._on_press = None
    mpt._on_release = None
    mpt._core = None
    return mpt


TARGET = object()
OTHER_BUTTON = object()
ENTER_TARGET = object()


class TestMousePushToTalkHoldMode:
    def test_press_then_release_drives_the_shared_core(self):
        rec = Recorder()
        mpt = _bare("hold", target=TARGET)
        mpt._on_press, mpt._on_release = rec.press, rec.release
        mpt._core = PushToTalkCore(rec.press, rec.release, None)

        mpt.handle_click(0, 0, TARGET, True)
        mpt.handle_click(0, 0, TARGET, False)
        assert rec.events == ["press", "release"]

    def test_other_buttons_are_ignored(self):
        rec = Recorder()
        mpt = _bare("hold", target=TARGET)
        mpt._core = PushToTalkCore(rec.press, rec.release, None)

        mpt.handle_click(0, 0, OTHER_BUTTON, True)
        mpt.handle_click(0, 0, OTHER_BUTTON, False)
        assert rec.events == []

    def test_injected_events_are_ignored(self):
        rec = Recorder()
        mpt = _bare("hold", target=TARGET)
        mpt._core = PushToTalkCore(rec.press, rec.release, None)

        mpt.handle_click(0, 0, TARGET, True, injected=True)
        mpt.handle_click(0, 0, TARGET, False, injected=True)
        assert rec.events == []

    def test_auto_repeat_press_fires_press_once(self):
        rec = Recorder()
        mpt = _bare("hold", target=TARGET)
        mpt._core = PushToTalkCore(rec.press, rec.release, None)

        mpt.handle_click(0, 0, TARGET, True)
        mpt.handle_click(0, 0, TARGET, True)
        mpt.handle_click(0, 0, TARGET, False)
        assert rec.events == ["press", "release"]


class TestMousePushToTalkToggleMode:
    def test_first_click_starts_second_click_stops(self):
        rec = Recorder()
        mpt = _bare("toggle", target=TARGET)
        mpt._on_press, mpt._on_release = rec.press, rec.release

        mpt.handle_click(0, 0, TARGET, True)
        mpt.handle_click(0, 0, TARGET, True)
        assert rec.events == ["press", "release"]

    def test_release_events_are_ignored(self):
        rec = Recorder()
        mpt = _bare("toggle", target=TARGET)
        mpt._on_press, mpt._on_release = rec.press, rec.release

        mpt.handle_click(0, 0, TARGET, False)
        assert rec.events == []

    def test_other_buttons_are_ignored(self):
        rec = Recorder()
        mpt = _bare("toggle", target=TARGET)
        mpt._on_press, mpt._on_release = rec.press, rec.release

        mpt.handle_click(0, 0, OTHER_BUTTON, True)
        assert rec.events == []


class TestMousePushToTalkEnterButton:
    def test_press_of_enter_button_invokes_on_enter(self):
        calls = []
        mpt = _bare("hold", target=TARGET, enter_target=ENTER_TARGET)
        mpt.on_enter = lambda: calls.append("enter")
        mpt._core = PushToTalkCore(lambda: None, lambda: None, None)

        mpt.handle_click(0, 0, ENTER_TARGET, True)
        assert calls == ["enter"]

    def test_release_of_enter_button_does_not_invoke_on_enter(self):
        calls = []
        mpt = _bare("hold", target=TARGET, enter_target=ENTER_TARGET)
        mpt.on_enter = lambda: calls.append("enter")
        mpt._core = PushToTalkCore(lambda: None, lambda: None, None)

        mpt.handle_click(0, 0, ENTER_TARGET, False)
        assert calls == []

    def test_enter_button_does_not_affect_main_button_recording(self):
        rec = Recorder()
        calls = []
        mpt = _bare("hold", target=TARGET, enter_target=ENTER_TARGET)
        mpt.on_enter = lambda: calls.append("enter")
        mpt._core = PushToTalkCore(rec.press, rec.release, None)

        mpt.handle_click(0, 0, ENTER_TARGET, True)
        assert rec.events == []
        assert calls == ["enter"]


class TestMousePushToTalkConstruction:
    def test_unsupported_button_raises_before_touching_pynput_backend(self):
        # "left"/"right" fail the name check in `resolve_mouse_button` before
        # any platform-specific `pynput.mouse.Button` lookup, so this is
        # portable across platforms/CI (unlike x1/x2, whose availability is
        # backend-dependent -- covered instead by `TestResolveMouseButton`).
        with pytest.raises(HotkeyBackendMissingError, match="Unsupported mouse button"):
            MousePushToTalk(button="left")

    def test_default_mode_is_hold(self):
        mpt = MousePushToTalk(button="middle")
        assert mpt.mode == "hold"

    def test_no_enter_button_leaves_enter_target_unset(self):
        mpt = MousePushToTalk(button="middle")
        assert mpt._enter_target is None


class TestCreateMouseListener:
    def test_returns_none_when_mouse_button_unset(self):
        assert create_mouse_listener(_config()) is None

    def test_dispatches_to_mouse_push_to_talk(self, monkeypatch):
        import local_flow.hotkeys.mouse as mouse_mod

        created = {}

        class FakeMouseListener:
            def __init__(self, button, mode, enter_button):
                created.update(button=button, mode=mode, enter_button=enter_button)

        monkeypatch.setattr(mouse_mod, "MousePushToTalk", FakeMouseListener)
        listener = create_mouse_listener(
            _config(mouse_button="middle", mouse_mode="toggle", mouse_enter_button="x1")
        )
        assert isinstance(listener, FakeMouseListener)
        assert created == {"button": "middle", "mode": "toggle", "enter_button": "x1"}


class TestMouseConfigValidation:
    """`mouse_button`/`mouse_mode`/`mouse_enter_button`: see `local_flow.config`."""

    def test_defaults(self):
        config = load_config(env={})
        assert config.mouse_button == ""
        assert config.mouse_mode == "hold"
        assert config.mouse_enter_button == ""

    def test_valid_mouse_button_values_accepted(self):
        for value in ("middle", "x1", "x2"):
            assert load_config(env={"LOCAL_FLOW_MOUSE_BUTTON": value}).mouse_button == value

    def test_left_rejected_at_load(self):
        with pytest.raises(ConfigError, match="mouse_button") as excinfo:
            load_config(env={"LOCAL_FLOW_MOUSE_BUTTON": "left"})
        assert "non-primary" in excinfo.value.hint

    def test_right_rejected_at_load(self):
        with pytest.raises(ConfigError, match="mouse_button") as excinfo:
            load_config(env={"LOCAL_FLOW_MOUSE_BUTTON": "right"})
        assert "non-primary" in excinfo.value.hint

    def test_invalid_mouse_mode_rejected(self):
        with pytest.raises(ConfigError, match="mouse_mode") as excinfo:
            load_config(env={"LOCAL_FLOW_MOUSE_MODE": "double-click"})
        message = str(excinfo.value)
        assert "hold" in message and "toggle" in message

    def test_valid_toggle_mode_accepted(self):
        assert load_config(env={"LOCAL_FLOW_MOUSE_MODE": "toggle"}).mouse_mode == "toggle"

    def test_mouse_enter_button_rejects_left_and_right(self):
        with pytest.raises(ConfigError, match="mouse_enter_button") as excinfo:
            load_config(env={"LOCAL_FLOW_MOUSE_ENTER_BUTTON": "right"})
        assert "non-primary" in excinfo.value.hint

    def test_mouse_enter_button_accepts_x2(self):
        config = load_config(env={"LOCAL_FLOW_MOUSE_ENTER_BUTTON": "x2"})
        assert config.mouse_enter_button == "x2"


class FakeReporter(StatusReporter):
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def notify(self, state, detail: str = "") -> None:
        self.events.append((state, detail))


class SignalingSink(FakeTextSink):
    """A `FakeTextSink` that signals an event on `press_key`, so a test on the
    main thread can wait for work enqueued on `CallbackDispatcher`'s
    background worker thread instead of racing it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.done = threading.Event()

    def press_key(self, key: str) -> None:
        super().press_key(key)
        self.done.set()


def _pipeline(tmp_path, sink):
    store = PersonalizationStore(tmp_path / "data")
    return DictationPipeline(
        transcriber=MockTranscriber(["hello there"]),
        polisher=TranscriptPolisher(MockChatClient(["Hello there."]), store),
        store=store,
        sink=sink,
    )


class _FakeSource:
    def record_until(self, stop, frame_ms):
        stop.wait(timeout=5)
        return b"pcm-bytes"


class TestRunLoopMousePushToTalk:
    """`_run_loop`'s push-to-talk branch (`local_flow.app`): the mouse
    listener runs on a daemon thread started before the blocking keyboard
    `listener.run()`, and both share the exact same dispatcher-wrapped
    start/finish callbacks.
    """

    def test_mouse_click_drives_the_same_finish_flow_as_the_keyboard(
        self, tmp_path, monkeypatch
    ):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        reporter = FakeReporter()
        config = load_config(
            env={"LOCAL_FLOW_MOUSE_BUTTON": "middle", "LOCAL_FLOW_MOUSE_MODE": "hold"}
        )
        done = threading.Event()

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "idle":
                    done.set()

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                # The keyboard side never itself fires; it just blocks like
                # the real listener until the mouse-triggered flow finishes.
                done.wait(timeout=5)

        class FakeMouseListener:
            def run(self, on_press, on_release):
                on_press()
                on_release()

        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config: FakeKeyboardListener(),
        )
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_mouse_listener",
            lambda config: FakeMouseListener(),
        )

        _run_loop(
            config,
            "push-to-talk",
            SignalingReporter(),
            dependencies=RunDependencies(pipeline, _FakeSource(), None),
        )

        assert done.is_set(), "the mouse-driven finish() never notified 'idle'"
        assert [event[0] for event in reporter.events] == [
            "recording",
            "processing",
            "inserted",
            "idle",
        ]
        assert sink.text  # the mouse click's utterance was actually inserted

    def test_mouse_enter_button_presses_enter_through_the_pipeline_sink(
        self, tmp_path, monkeypatch
    ):
        sink = SignalingSink()
        pipeline = _pipeline(tmp_path, sink)
        config = load_config(
            env={
                "LOCAL_FLOW_MOUSE_BUTTON": "middle",
                "LOCAL_FLOW_MOUSE_ENTER_BUTTON": "x1",
            }
        )

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                return  # returns immediately; the mouse fake does the work

        class FakeMouseListener:
            def __init__(self) -> None:
                self.on_enter = None

            def run(self, on_press, on_release):
                assert self.on_enter is not None
                self.on_enter()

        fake_mouse = FakeMouseListener()
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config: FakeKeyboardListener(),
        )
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_mouse_listener",
            lambda config: fake_mouse,
        )

        _run_loop(
            config,
            "push-to-talk",
            FakeReporter(),
            dependencies=RunDependencies(pipeline, _FakeSource(), None),
        )

        assert sink.done.wait(timeout=2), "on_enter never reached sink.press_key"
        assert sink.events == [("key", "enter")]

    def test_no_mouse_notice_when_mouse_button_unset(self, tmp_path, monkeypatch, capsys):
        """`mouse_button=""` (the default): the real `create_mouse_listener`
        returns `None` without importing `local_flow.hotkeys.mouse`, so no
        thread starts and no mouse notice prints -- byte-identical to before
        mouse push-to-talk existed. Only the keyboard listener is faked here;
        `create_mouse_listener` runs for real.
        """
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        config = load_config(env={})

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                pass

        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config: FakeKeyboardListener(),
        )

        _run_loop(
            config,
            "push-to-talk",
            FakeReporter(),
            dependencies=RunDependencies(pipeline, _FakeSource(), None),
        )

        assert "mouse push-to-talk" not in capsys.readouterr().out
