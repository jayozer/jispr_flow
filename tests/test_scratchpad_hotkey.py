"""Scratchpad dictate-to-pad hotkey (Phase 7 E13): `RunDependencies.scratchpad_sink`,
`_handle_utterance`'s `sink_override`, and `_run_loop`'s toggle wiring.

Mirrors `tests/test_transform_command_hotkeys.py`'s patterns for the
transform/command hotkeys (`TapListener` on a daemon thread, dispatcher-
wrapped, disabled entirely when the hotkey is unset).
"""

import threading
import time

from local_flow.app import (
    RunDependencies,
    _build_pipeline,
    _handle_utterance,
    _run_loop,
    _run_scratchpad_listener,
)
from local_flow.asr.mock import MockTranscriber
from local_flow.config import load_config
from local_flow.errors import HotkeyBackendMissingError, LocalFlowError
from local_flow.insertion.base import FakeTextSink
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.scratchpad.sink import ScratchpadSink
from local_flow.scratchpad.store import NoteStore
from local_flow.status import StatusReporter


def _config(**env):
    return load_config(env={f"LOCAL_FLOW_{k.upper()}": v for k, v in env.items()})


class FakeReporter(StatusReporter):
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def notify(self, state, detail: str = "") -> None:
        self.events.append((state, detail))


def _pipeline(tmp_path, sink, llm=None, transcriber=None):
    store = PersonalizationStore(tmp_path / "data")
    llm = llm if llm is not None else MockChatClient(["ok"])
    return DictationPipeline(
        transcriber=transcriber or MockTranscriber(["hello"]),
        polisher=TranscriptPolisher(llm, store),
        store=store,
        sink=sink,
    )


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestHandleUtteranceSinkOverride:
    """`_handle_utterance`'s `sink_override`: only passed to
    `pipeline.process_audio` when not `None`, so existing 2-arg test doubles
    (see test_status.py's `_RecordingPipeline`) are unaffected.
    """

    def test_none_calls_process_audio_with_two_positional_args_only(self, tmp_path):
        calls = []

        class _Recorder:
            def process_audio(self, pcm, sample_rate):
                calls.append((pcm, sample_rate))
                from local_flow.pipeline import DictationResult

                return DictationResult(rough="", cleaned="", polished="", final="")

        _handle_utterance(_Recorder(), FakeReporter(), b"pcm", 16000)

        assert calls == [(b"pcm", 16000)]

    def test_given_override_is_forwarded_as_keyword(self, tmp_path):
        received = {}

        class _Recorder:
            def process_audio(self, pcm, sample_rate, sink_override=None):
                received["sink_override"] = sink_override
                from local_flow.pipeline import DictationResult

                return DictationResult(rough="", cleaned="", polished="", final="")

        sentinel = FakeTextSink()
        _handle_utterance(_Recorder(), FakeReporter(), b"pcm", 16000, sink_override=sentinel)

        assert received["sink_override"] is sentinel

    def test_real_pipeline_routes_insertion_to_the_override_sink(self, tmp_path):
        normal_sink = FakeTextSink()
        pipeline = _pipeline(
            tmp_path, normal_sink, llm=MockChatClient(["ok"]),
            transcriber=MockTranscriber(["hello there"]),
        )
        note_store = NoteStore(tmp_path / "data")
        pad_sink = ScratchpadSink(note_store)

        _handle_utterance(pipeline, FakeReporter(), b"pcm", 16000, sink_override=pad_sink)

        assert normal_sink.events == []
        assert note_store.read(note_store.active_note()) == "ok"


class TestRunLoopScratchpadHotkeyDisabledByDefault:
    def test_disabled_by_default_never_constructs_a_tap_listener(self, tmp_path, monkeypatch):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        config = _config()  # scratchpad_hotkey unset
        reporter = FakeReporter()
        constructed = []

        class SpyTapListener:
            def __init__(self, key_name):
                constructed.append(key_name)

            def run(self, on_tap):
                pass

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                return

        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", SpyTapListener)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        _run_loop(
            config, "push-to-talk", reporter,
            dependencies=RunDependencies(pipeline, None, None),
        )

        assert constructed == []
        assert not any("scratchpad" in detail for _state, detail in reporter.events)

    def test_hotkey_set_without_a_sink_disables_with_startup_warning(
        self, tmp_path, monkeypatch
    ):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        config = _config(scratchpad_hotkey="f8")  # no scratchpad_sink on deps
        reporter = FakeReporter()
        constructed = []

        class SpyTapListener:
            def __init__(self, key_name):
                constructed.append(key_name)

            def run(self, on_tap):
                pass

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                return

        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", SpyTapListener)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        _run_loop(
            config, "push-to-talk", reporter,
            dependencies=RunDependencies(pipeline, None, None),  # scratchpad_sink=None
        )

        assert constructed == []
        warnings = [d for s, d in reporter.events if s == "warning"]
        assert any("no scratchpad sink" in w for w in warnings)


class TestRunLoopScratchpadHotkeyUnsupportedKey:
    """Review item 14 (scratchpad leg): a distinct-but-unsupported
    `scratchpad_hotkey` value must disable just this hotkey with an
    actionable warning instead of aborting the whole app -- see
    tests/test_transform_command_hotkeys.py for the transform/command legs.
    """

    def test_unsupported_scratchpad_hotkey_warns_and_keeps_running(
        self, tmp_path, monkeypatch
    ):
        sink = FakeTextSink()
        pipeline = _pipeline(tmp_path, sink)
        scratchpad_sink = ScratchpadSink(NoteStore(tmp_path / "data"))
        config = _config(hotkey="f9", scratchpad_hotkey="fn")
        reporter = FakeReporter()
        main_listener_ran = threading.Event()

        class RaisingTapListener:
            def __init__(self, key_name):
                raise HotkeyBackendMissingError(
                    f"Unknown hotkey {key_name!r}.",
                    hint="Use a pynput key name such as f9, f8, scroll_lock, "
                    "or a single character.",
                )

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                main_listener_ran.set()

        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", RaisingTapListener)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        result = _run_loop(
            config, "push-to-talk", reporter,
            dependencies=RunDependencies(
                pipeline, None, None, scratchpad_sink=scratchpad_sink
            ),
        )

        assert result == 0  # the app did not abort
        assert main_listener_ran.is_set()  # the main hotkey still ran
        warnings = [d for s, d in reporter.events if s == "warning"]
        assert any("scratchpad hotkey disabled" in w for w in warnings)
        assert any("'fn'" in w for w in warnings)  # names the bad value


class TestRunLoopScratchpadHotkeyToggle:
    """End-to-end: tapping the scratchpad hotkey toggles `pad_active`, which
    routes the NEXT utterance's insertion (and only that sink -- the normal
    sink is left untouched) into the scratchpad; a second tap reverts.
    """

    def test_tap_on_then_dictate_routes_to_scratchpad_not_normal_sink(
        self, tmp_path, monkeypatch
    ):
        normal_sink = FakeTextSink()
        llm = MockChatClient(["first utterance"])
        pipeline = _pipeline(
            tmp_path, normal_sink, llm=llm, transcriber=MockTranscriber(["first utterance"])
        )
        config = _config(scratchpad_hotkey="f8")
        reporter = FakeReporter()
        note_store = NoteStore(tmp_path / "pad-data")
        scratchpad_sink = ScratchpadSink(note_store)
        done = threading.Event()

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "idle":
                    done.set()

        class FakeTapListener:
            def __init__(self, key_name):
                self.key_name = key_name

            def run(self, on_tap):
                on_tap()  # toggle ON

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                assert _wait_until(
                    lambda: any("scratchpad on" in d for _s, d in reporter.events)
                ), "scratchpad toggle-on notification never arrived"
                on_press()
                on_release()
                done.wait(timeout=5)

        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", FakeTapListener)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        class _FakeSource:
            def record_until(self, stop, frame_ms):
                stop.wait(timeout=5)
                return b"pcm-bytes"

        _run_loop(
            config, "push-to-talk", SignalingReporter(),
            dependencies=RunDependencies(
                pipeline, _FakeSource(), None, scratchpad_sink=scratchpad_sink
            ),
        )

        assert done.wait(timeout=2)
        assert normal_sink.events == []
        assert note_store.read(note_store.active_note()) == "first utterance"

    def test_tap_twice_reverts_to_normal_sink(self, tmp_path, monkeypatch):
        normal_sink = FakeTextSink()
        llm = MockChatClient(["second utterance"])
        pipeline = _pipeline(
            tmp_path, normal_sink, llm=llm, transcriber=MockTranscriber(["second utterance"])
        )
        config = _config(scratchpad_hotkey="f8")
        reporter = FakeReporter()
        note_store = NoteStore(tmp_path / "pad-data")
        scratchpad_sink = ScratchpadSink(note_store)
        done = threading.Event()

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "idle":
                    done.set()

        class FakeTapListener:
            def __init__(self, key_name):
                pass

            def run(self, on_tap):
                on_tap()  # ON
                on_tap()  # OFF

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                assert _wait_until(
                    lambda: any("scratchpad off" in d for _s, d in reporter.events)
                ), "scratchpad toggle-off notification never arrived"
                on_press()
                on_release()
                done.wait(timeout=5)

        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", FakeTapListener)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        class _FakeSource:
            def record_until(self, stop, frame_ms):
                stop.wait(timeout=5)
                return b"pcm-bytes"

        _run_loop(
            config, "push-to-talk", SignalingReporter(),
            dependencies=RunDependencies(
                pipeline, _FakeSource(), None, scratchpad_sink=scratchpad_sink
            ),
        )

        assert done.wait(timeout=2)
        assert normal_sink.events == [("insert", "second utterance")]
        assert note_store.read(note_store.active_note()) == ""


class TestRunLoopScratchpadHotkeyToggleMidRecording:
    """Same end-to-end machinery as `TestRunLoopScratchpadHotkeyToggle`, but
    the tap lands BETWEEN `on_press()` (recording started) and
    `on_release()` (utterance processed) instead of strictly before either.
    Pins that `finish()` reads `pad_active[0]` at the moment IT runs, not
    whatever it was when the recording started: since `_toggle_pad` and
    `finish` are wrapped by the very same `CallbackDispatcher` (a single
    FIFO worker thread -- see `_toggle_pad`'s docstring), the toggle
    (enqueued and fully processed here before `on_release()` is even called)
    is guaranteed to have already applied by the time `finish()` runs,
    regardless of how the tap and the press/release interleave in real
    time.
    """

    def test_toggle_mid_recording_still_routes_that_utterance_to_pad(
        self, tmp_path, monkeypatch
    ):
        normal_sink = FakeTextSink()
        llm = MockChatClient(["mid utterance"])
        pipeline = _pipeline(
            tmp_path, normal_sink, llm=llm, transcriber=MockTranscriber(["mid utterance"])
        )
        config = _config(scratchpad_hotkey="f8")
        reporter = FakeReporter()
        note_store = NoteStore(tmp_path / "pad-data")
        scratchpad_sink = ScratchpadSink(note_store)
        done = threading.Event()
        fire_tap = threading.Event()

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "idle":
                    done.set()

        class FakeTapListener:
            def __init__(self, key_name):
                pass

            def run(self, on_tap):
                # Held back until the keyboard listener confirms the
                # recording has actually started (below) -- pins the tap
                # landing strictly AFTER `start()`, not before it.
                fire_tap.wait(timeout=5)
                on_tap()

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                on_press()  # enqueue start(): recording begins
                assert _wait_until(
                    lambda: any(state == "recording" for state, _d in reporter.events)
                ), "recording never started"
                fire_tap.set()  # let the tap fire now, mid-recording
                assert _wait_until(
                    lambda: any("scratchpad on" in d for _s, d in reporter.events)
                ), "scratchpad toggle-on notification never arrived"
                on_release()  # enqueue finish(): only now does it process
                done.wait(timeout=5)

        monkeypatch.setattr("local_flow.hotkeys.base.TapListener", FakeTapListener)
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeKeyboardListener(),
        )

        class _FakeSource:
            def record_until(self, stop, frame_ms):
                # Blocks until `finish()` (run only after the toggle has
                # already landed) sets `stop` -- so the utterance is
                # "captured" strictly after the toggle applied.
                stop.wait(timeout=5)
                return b"pcm-bytes"

        _run_loop(
            config, "push-to-talk", SignalingReporter(),
            dependencies=RunDependencies(
                pipeline, _FakeSource(), None, scratchpad_sink=scratchpad_sink
            ),
        )

        assert done.wait(timeout=2)
        assert normal_sink.events == []
        assert note_store.read(note_store.active_note()) == "mid utterance"


class TestRunScratchpadListenerErrorVisible:
    """An uncaught exception on the scratchpad hotkey's daemon thread is
    silently swallowed by Python; `_run_scratchpad_listener` must catch
    `LocalFlowError` and print it in `_fail`'s format instead.
    """

    def test_local_flow_error_prints_formatted_message_with_hint(self, capsys):
        class FailingListener:
            def run(self, on_tap):
                raise HotkeyBackendMissingError("boom", hint="fix it")

        _run_scratchpad_listener(FailingListener(), lambda: None)

        captured = capsys.readouterr()
        assert "error: scratchpad hotkey stopped: boom" in captured.err
        assert "hint : fix it" in captured.err

    def test_error_without_hint_omits_hint_line(self, capsys):
        class FailingListener:
            def run(self, on_tap):
                raise LocalFlowError("boom")

        _run_scratchpad_listener(FailingListener(), lambda: None)

        captured = capsys.readouterr()
        assert "error: scratchpad hotkey stopped: boom" in captured.err
        assert "hint :" not in captured.err

    def test_clean_run_prints_nothing(self, capsys):
        class QuietListener:
            def run(self, on_tap):
                return

        _run_scratchpad_listener(QuietListener(), lambda: None)

        assert capsys.readouterr().err == ""


class TestBuildRunDependenciesScratchpadSink:
    def test_build_run_dependencies_always_builds_a_scratchpad_sink(self, tmp_path, monkeypatch):
        from local_flow.app import _build_run_dependencies

        config = _config(
            data_dir=str(tmp_path), asr_backend="mock", lmstudio_base_url="http://127.0.0.1:1/v1"
        )
        deps = _build_run_dependencies(config)

        assert isinstance(deps.scratchpad_sink, ScratchpadSink)


class TestBuildPipelineStillWorksWithScratchpadFields:
    """Sanity check that pipeline building is unaffected by these changes."""

    def test_build_pipeline_smoke(self, tmp_path):
        data_dir = tmp_path / "data"
        config = _config(
            data_dir=str(data_dir), asr_backend="mock", lmstudio_base_url="http://127.0.0.1:1/v1"
        )
        chat_client = MockChatClient(["ok"])
        sink = FakeTextSink()
        pipeline = _build_pipeline(config, chat_client, sink)
        assert pipeline.sink is sink
