"""Tests for the StatusReporter seam: ConsoleReporter output mapping and the
extracted per-utterance handler in ``local_flow.app``.
"""

import array
import threading

from local_flow.app import (
    RunDependencies,
    _build_run_dependencies,
    _build_vad,
    _handle_utterance,
    _interruptible,
    _run_loop,
    parse_mic_priority,
)
from local_flow.asr.mock import MockStream, MockTranscriber
from local_flow.audio.gain import normalize_peak
from local_flow.audio.vad import EnergyVAD
from local_flow.commands.command_mode import CommandMode
from local_flow.config import load_config
from local_flow.demo import synth_pcm
from local_flow.errors import LMStudioConnectionError, LocalFlowError
from local_flow.insertion.base import FakeTextSink
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline, DictationResult
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.status import ConsoleReporter, StatusReporter


class FailingChatClient(MockChatClient):
    """Simulates LM Studio being down (same idiom as test_pipeline_integration)."""

    def chat(self, messages, *, temperature=0.2, max_tokens=None):
        raise LMStudioConnectionError(
            "Could not reach LM Studio at http://localhost:1234/v1: refused"
        )


class FakeReporter(StatusReporter):
    """Collects (state, detail) tuples in emission order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def notify(self, state, detail: str = "") -> None:
        self.events.append((state, detail))


def _make_pipeline(store, llm, sink, transcriber=None):
    return DictationPipeline(
        transcriber=transcriber or MockTranscriber(["placeholder"]),
        polisher=TranscriptPolisher(llm, store),
        store=store,
        sink=sink,
        command_mode=CommandMode(llm, dictionary_terms=store.dictionary_terms()),
    )


class TestConsoleReporter:
    """Reproduces today's CLI output byte-for-byte."""

    def test_warning_prints_to_stderr(self, capsys):
        ConsoleReporter().notify("warning", "LM Studio polish skipped")
        captured = capsys.readouterr()
        assert captured.err == "warning: LM Studio polish skipped\n"
        assert captured.out == ""

    def test_inserted_prints_to_stdout(self, capsys):
        ConsoleReporter().notify("inserted", repr("send the invoice"))
        captured = capsys.readouterr()
        assert captured.out == "inserted: 'send the invoice'\n"
        assert captured.err == ""

    def test_error_prints_to_stderr(self, capsys):
        ConsoleReporter().notify("error", "Fake sink was configured to fail.")
        captured = capsys.readouterr()
        assert captured.err == "error: Fake sink was configured to fail.\n"
        assert captured.out == ""

    def test_recording_is_silent(self, capsys):
        ConsoleReporter().notify("recording")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_processing_is_silent(self, capsys):
        ConsoleReporter().notify("processing")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_idle_is_silent(self, capsys):
        ConsoleReporter().notify("idle")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_preview_prints_carriage_return_prefixed_line_to_stderr(self, capsys):
        ConsoleReporter().notify("preview", "rough partial")
        captured = capsys.readouterr()
        assert captured.err == "\r… rough partial"
        assert captured.out == ""

    def test_preview_truncates_detail_to_70_chars(self, capsys):
        long_text = "a" * 100
        ConsoleReporter().notify("preview", long_text)
        captured = capsys.readouterr()
        assert captured.err == "\r… " + "a" * 70

    def test_warning_after_preview_emits_leading_newline(self, capsys):
        reporter = ConsoleReporter()
        reporter.notify("preview", "rough")
        reporter.notify("warning", "LM Studio polish skipped")
        captured = capsys.readouterr()
        assert captured.err == "\r… rough\nwarning: LM Studio polish skipped\n"

    def test_inserted_after_preview_emits_leading_newline_on_stderr_only(self, capsys):
        reporter = ConsoleReporter()
        reporter.notify("preview", "rough")
        reporter.notify("inserted", repr("send the invoice"))
        captured = capsys.readouterr()
        assert captured.err == "\r… rough\n"
        assert captured.out == "inserted: 'send the invoice'\n"

    def test_error_after_preview_emits_leading_newline(self, capsys):
        reporter = ConsoleReporter()
        reporter.notify("preview", "rough")
        reporter.notify("error", "boom")
        captured = capsys.readouterr()
        assert captured.err == "\r… rough\nerror: boom\n"

    def test_second_printed_state_after_preview_only_gets_one_newline(self, capsys):
        reporter = ConsoleReporter()
        reporter.notify("preview", "rough")
        reporter.notify("inserted", repr("first"))
        reporter.notify("inserted", repr("second"))
        captured = capsys.readouterr()
        # Only one leading "\n" for the *first* printed state after the
        # preview; nothing pending by the second "inserted".
        assert captured.err == "\r… rough\n"
        assert captured.out == "inserted: 'first'\ninserted: 'second'\n"

    def test_recording_processing_idle_do_not_clear_pending_preview(self, capsys):
        reporter = ConsoleReporter()
        reporter.notify("preview", "rough")
        reporter.notify("recording")
        reporter.notify("processing")
        reporter.notify("idle")
        reporter.notify("inserted", repr("done"))
        captured = capsys.readouterr()
        assert captured.err == "\r… rough\n"
        assert captured.out == "inserted: 'done'\n"

    def test_no_leading_newline_when_preview_never_fired(self, capsys):
        # Non-streaming byte-identical guarantee: with `_preview_pending`
        # never set to True, the printed states are unchanged from before
        # this feature existed.
        reporter = ConsoleReporter()
        reporter.notify("inserted", repr("send the invoice"))
        captured = capsys.readouterr()
        assert captured.out == "inserted: 'send the invoice'\n"
        assert captured.err == ""


class TestHandleUtterance:
    """Drives the extracted per-utterance handler with a FakeReporter."""

    def test_success_sequence_recording_processing_inserted_idle(self, tmp_path):
        store = PersonalizationStore(tmp_path / "data")
        llm = MockChatClient(["Send the invoice."])
        sink = FakeTextSink()
        pipeline = _make_pipeline(store, llm, sink)
        reporter = FakeReporter()

        # "recording" precedes the handler in the real run loop (emitted by
        # push-to-talk's start()); simulate that hand-off here.
        reporter.notify("recording")
        _handle_utterance(pipeline, reporter, b"pcm-bytes", sample_rate=16000)

        assert reporter.events == [
            ("recording", ""),
            ("processing", ""),
            ("inserted", repr("Send the invoice.")),
            ("idle", ""),
        ]

    def test_sequence_with_pipeline_warning(self, tmp_path):
        store = PersonalizationStore(tmp_path / "data")
        store.add_dictionary_term("PostgreSQL")
        sink = FakeTextSink()
        pipeline = _make_pipeline(
            store,
            FailingChatClient(),
            sink,
            transcriber=MockTranscriber(["email the postgresql team"]),
        )
        reporter = FakeReporter()

        reporter.notify("recording")
        _handle_utterance(pipeline, reporter, b"pcm-bytes", sample_rate=16000)

        states = [event[0] for event in reporter.events]
        assert states == ["recording", "processing", "warning", "inserted", "idle"]
        assert "LM Studio polish skipped" in reporter.events[2][1]
        assert reporter.events[3] == ("inserted", repr("email the PostgreSQL team"))

    def test_error_sequence_reports_error_and_still_reaches_idle(self, tmp_path, capsys):
        store = PersonalizationStore(tmp_path / "data")
        sink = FakeTextSink(fail=True)
        pipeline = _make_pipeline(store, MockChatClient(["polished text"]), sink)
        reporter = FakeReporter()

        _handle_utterance(pipeline, reporter, b"pcm-bytes", sample_rate=16000)

        assert reporter.events == [
            ("processing", ""),
            ("error", "Fake sink was configured to fail."),
            ("idle", ""),
        ]
        # The hint doesn't fit the single-string `notify(state, detail)`
        # interface, so it stays a literal print (unchanged from `_fail`).
        assert "hint : Test-only failure." in capsys.readouterr().err


class TestHandleUtteranceNonLocalFlowError:
    """Group C item 2: `_handle_utterance` must survive *any* exception, not
    just `LocalFlowError` -- a full-disk `OSError`, ctranslate2
    `RuntimeError`, or bad `asr_language` `ValueError` used to escape and
    kill the hands-free/tray session loop.
    """

    class _ExplodingTranscriber(MockTranscriber):
        """Raises a non-LocalFlowError, as a crashed ctranslate2 would."""

        def transcribe(self, pcm: bytes, sample_rate: int) -> str:
            self.calls.append((len(pcm), sample_rate))
            raise RuntimeError("ctranslate2 aborted")

    def test_runtime_error_reports_error_and_still_reaches_idle(self, tmp_path, capsys):
        store = PersonalizationStore(tmp_path / "data")
        pipeline = _make_pipeline(
            store,
            MockChatClient(["never reached"]),
            FakeTextSink(),
            transcriber=self._ExplodingTranscriber([]),
        )
        reporter = FakeReporter()

        _handle_utterance(pipeline, reporter, b"pcm-bytes", sample_rate=16000)

        assert [state for state, _ in reporter.events] == ["processing", "error", "idle"]
        assert reporter.events[1] == ("error", "RuntimeError: ctranslate2 aborted")
        # No curated hint exists for a non-LocalFlowError; the traceback goes
        # to stderr instead so the failure stays diagnosable.
        assert "RuntimeError: ctranslate2 aborted" in capsys.readouterr().err

    def test_runtime_error_leaves_the_wav_in_pending(self, tmp_path):
        from local_flow.audio.recovery import PendingAudioStore

        store = PersonalizationStore(tmp_path / "data")
        pending = PendingAudioStore(tmp_path / "recovery")
        pipeline = _make_pipeline(
            store,
            MockChatClient(["never reached"]),
            FakeTextSink(),
            transcriber=self._ExplodingTranscriber([]),
        )
        reporter = FakeReporter()

        # Even-length payload: WAV frames are 2 bytes each (16-bit mono).
        _handle_utterance(pipeline, reporter, b"pcm-data", 16000, pending)

        [saved_path] = pending.pending()
        saved_pcm, saved_rate = pending.load(saved_path)
        assert saved_pcm == b"pcm-data"
        assert saved_rate == 16000

    def test_failing_pending_save_is_reported_not_raised(self, tmp_path, capsys):
        """`pending_store.save()` now sits inside the guard: a full disk
        costs that one utterance (reported as "error"), not the session.
        """

        class FullDiskStore:
            def save(self, pcm: bytes, sample_rate: int):
                raise OSError(28, "No space left on device")

        store = PersonalizationStore(tmp_path / "data")
        pipeline = _make_pipeline(store, MockChatClient(["ok"]), FakeTextSink())
        reporter = FakeReporter()

        _handle_utterance(pipeline, reporter, b"pcm-bytes", 16000, FullDiskStore())

        assert [state for state, _ in reporter.events] == ["processing", "error", "idle"]
        assert "No space left on device" in reporter.events[1][1]
        capsys.readouterr()  # swallow the traceback; asserted in the test above


class DummyVAD:
    """Placeholder VAD passed through `_run_loop`'s `dependencies` tuple.

    `segment_stream` is monkeypatched in these tests, so the real VAD is
    never consulted.
    """


class TestHandsFreeReArmsRecording:
    """Carry-over from the T1 review: a tray reporter must see "recording"
    again before each subsequent hands-free utterance, not just the first.
    ConsoleReporter is silent for "recording", so CLI output is unaffected.
    """

    def test_recording_is_rearmed_before_the_second_utterance(self, tmp_path, monkeypatch):
        store = PersonalizationStore(tmp_path / "data")
        llm = MockChatClient(["First.", "Second."])
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["first", "second"]),
            polisher=TranscriptPolisher(llm, store),
            store=store,
            sink=sink,
            command_mode=CommandMode(llm, dictionary_terms=store.dictionary_terms()),
        )
        reporter = FakeReporter()
        config = load_config(env={})

        monkeypatch.setattr(
            "local_flow.audio.vad.segment_stream",
            lambda *args, **kwargs: iter([b"segment-one", b"segment-two"]),
        )

        class DummySource:
            def frames(self, frame_ms):
                return iter([])

        _run_loop(
            config,
            "hands-free",
            reporter,
            dependencies=RunDependencies(pipeline, DummySource(), DummyVAD()),
        )

        states = [event[0] for event in reporter.events]
        assert states == [
            "recording",
            "processing",
            "inserted",
            "idle",
            "recording",
            "processing",
            "inserted",
            "idle",
        ]


class TestHandsFreeLoopSurvivesTranscriberCrash:
    """Group C item 2, end to end: a transcriber raising a non-LocalFlowError
    mid-utterance must not kill the hands-free segment loop -- the error is
    reported, the failed utterance's WAV stays in `pending/`, and the *next*
    utterance still processes normally.
    """

    class _CrashOnceTranscriber(MockTranscriber):
        """RuntimeError on the first utterance, scripted text afterwards."""

        def transcribe(self, pcm: bytes, sample_rate: int) -> str:
            if not self.calls:
                self.calls.append((len(pcm), sample_rate))
                raise RuntimeError("ctranslate2 aborted")
            return super().transcribe(pcm, sample_rate)

    def test_error_on_first_utterance_second_still_inserts(
        self, tmp_path, monkeypatch, capsys
    ):
        from local_flow.audio.recovery import PendingAudioStore

        store = PersonalizationStore(tmp_path / "data")
        llm = MockChatClient(["Second."])
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=self._CrashOnceTranscriber(["second"]),
            polisher=TranscriptPolisher(llm, store),
            store=store,
            sink=sink,
            command_mode=CommandMode(llm, dictionary_terms=store.dictionary_terms()),
        )
        reporter = FakeReporter()
        config = load_config(env={})
        pending = PendingAudioStore(tmp_path / "recovery")

        # Even-length segments: WAV frames are 2 bytes each (16-bit mono).
        monkeypatch.setattr(
            "local_flow.audio.vad.segment_stream",
            lambda *args, **kwargs: iter([b"segment-1st!", b"segment-2nd!"]),
        )

        class DummySource:
            def frames(self, frame_ms):
                return iter([])

        result = _run_loop(
            config,
            "hands-free",
            reporter,
            dependencies=RunDependencies(pipeline, DummySource(), DummyVAD(), pending),
        )

        assert result == 0
        assert [event[0] for event in reporter.events] == [
            "recording",
            "processing",
            "error",
            "idle",
            "recording",
            "processing",
            "inserted",
            "idle",
        ]
        assert sink.events == [("insert", "Second.")]
        # The crashed utterance's WAV survives for `local-flow recover`; the
        # successful one deleted its own on the way out.
        [saved_path] = pending.pending()
        assert pending.load(saved_path) == (b"segment-1st!", config.sample_rate)
        capsys.readouterr()  # swallow the traceback printed for the RuntimeError


class TestCancelPathNotifiesIdle:
    """Carry-over from the T1 review: cancelling a push-to-talk dictation
    must also notify "idle" (silent on the console; makes a tray reporter
    return to its idle icon), in addition to the existing literal print.
    """

    def test_cancel_prints_discarded_and_notifies_idle(self, tmp_path, monkeypatch, capsys):
        store = PersonalizationStore(tmp_path / "data")
        llm = MockChatClient(["ok"])
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=TranscriptPolisher(llm, store),
            store=store,
            sink=sink,
            command_mode=CommandMode(llm, dictionary_terms=store.dictionary_terms()),
        )
        reporter = FakeReporter()
        config = load_config(env={})
        done = threading.Event()

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "idle":
                    done.set()

        class FakeSource:
            def frames(self, frame_ms):
                return iter([])

            def record_until(self, stop, frame_ms):
                stop.wait(timeout=5)
                return b""

        class FakeListener:
            def run(self, on_press, on_release, on_cancel):
                on_press()
                on_cancel()
                done.wait(timeout=5)

        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeListener(),
        )

        _run_loop(
            config,
            "push-to-talk",
            SignalingReporter(),
            dependencies=RunDependencies(pipeline, FakeSource(), DummyVAD()),
        )

        assert done.is_set(), "cancel() never notified 'idle'"
        assert reporter.events[-1] == ("idle", "")
        assert "dictation discarded" in capsys.readouterr().out


class TestAppLevelCancelGate:
    """`_run_loop`'s `recording_active` Event is threaded into
    `create_hotkey_listener` as `cancel_gate`, so the keyboard listener's
    cancel key can discard a recording that a *different* listener (mouse
    push-to-talk) started, and `cancel()` itself no-ops (no print, no join)
    when nothing is actually recording -- e.g. an idle Esc press.
    """

    def test_mouse_started_recording_is_cancelled_via_the_keyboard_gate(
        self, tmp_path, monkeypatch, capsys
    ):
        store = PersonalizationStore(tmp_path / "data")
        llm = MockChatClient(["ok"])
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=TranscriptPolisher(llm, store),
            store=store,
            sink=sink,
            command_mode=CommandMode(llm, dictionary_terms=store.dictionary_terms()),
        )
        reporter = FakeReporter()
        config = load_config(env={"LOCAL_FLOW_MOUSE_BUTTON": "middle"})
        recording_started = threading.Event()
        done = threading.Event()
        captured_gate: dict[str, object] = {}

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "recording":
                    recording_started.set()
                if state == "idle":
                    done.set()

        class FakeSource:
            def frames(self, frame_ms):
                return iter([])

            def record_until(self, stop, frame_ms):
                stop.wait(timeout=5)
                return b""

        class FakeKeyboardListener:
            def run(self, on_press, on_release, on_cancel):
                # Stands in for a real listener whose own key was never
                # held: the mouse started the recording, not this listener,
                # so in production `PushToTalkCore.cancel_down` only calls
                # `on_cancel` because `cancel_gate()` (recording_active) is
                # True -- simulated here by calling it directly once that
                # gate would actually be True.
                assert recording_started.wait(timeout=5), "mouse never started the recording"
                on_cancel()
                done.wait(timeout=5)

        class FakeMouseListener:
            def run(self, on_press, on_release):
                on_press()

        def fake_create_hotkey_listener(config, cancel_gate=None):
            captured_gate["gate"] = cancel_gate
            return FakeKeyboardListener()

        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            fake_create_hotkey_listener,
        )
        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_mouse_listener",
            lambda config: FakeMouseListener(),
        )

        _run_loop(
            config,
            "push-to-talk",
            SignalingReporter(),
            dependencies=RunDependencies(pipeline, FakeSource(), DummyVAD()),
        )

        assert done.is_set(), "cancel() never notified 'idle'"
        assert captured_gate["gate"] is not None
        assert captured_gate["gate"]() is False  # cancel() cleared recording_active
        assert [event[0] for event in reporter.events] == ["recording", "idle"]
        assert "dictation discarded" in capsys.readouterr().out

    def test_idle_cancel_key_press_is_silent(self, tmp_path, monkeypatch, capsys):
        """No recording has started yet: invoking `on_cancel` must be a pure
        no-op -- no print, no `reporter.notify("idle")`. Calls `on_press()`/
        `on_release()` afterward as both an ordering fence (the dispatcher's
        single worker thread runs callbacks FIFO, so once "recording" is
        observed the earlier `on_cancel()` has already finished running) and
        normal cleanup of the recorder thread it starts.
        """
        store = PersonalizationStore(tmp_path / "data")
        llm = MockChatClient(["ok"])
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=TranscriptPolisher(llm, store),
            store=store,
            sink=sink,
            command_mode=CommandMode(llm, dictionary_terms=store.dictionary_terms()),
        )
        reporter = FakeReporter()
        config = load_config(env={})
        done = threading.Event()

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "idle":
                    done.set()

        class FakeSource:
            def frames(self, frame_ms):
                return iter([])

            def record_until(self, stop, frame_ms):
                stop.wait(timeout=5)
                return b"pcm-bytes"  # non-empty: finish() must actually process it

        class FakeListener:
            def run(self, on_press, on_release, on_cancel):
                on_cancel()  # Esc while nothing is recording: must no-op
                on_press()
                on_release()
                done.wait(timeout=5)

        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeListener(),
        )

        _run_loop(
            config,
            "push-to-talk",
            SignalingReporter(),
            dependencies=RunDependencies(pipeline, FakeSource(), DummyVAD()),
        )

        assert done.is_set(), "the post-cancel press/release never notified 'idle'"
        # Exactly one recording cycle's worth of events: had the idle Esc
        # actually fired `cancel()`, an extra ("idle", "") would appear
        # before "recording", and "dictation discarded" would be printed.
        assert [event[0] for event in reporter.events] == [
            "recording",
            "processing",
            "inserted",
            "idle",
        ]
        assert "dictation discarded" not in capsys.readouterr().out


class TestInterruptible:
    """`_interruptible` wraps the raw mic-frame iterator so hands-free Stop
    takes effect within one frame, even while `segment_stream` is buffering
    silence and would otherwise never hand control back to `_run_loop`'s
    loop (carry-over review fix: clicking Stop during silence used to block
    indefinitely).
    """

    def test_stops_mid_iteration_once_the_event_is_set(self):
        stop_event = threading.Event()

        def frames():
            yield b"frame-1"
            yield b"frame-2"
            stop_event.set()
            yield b"frame-3"
            yield b"frame-4"

        assert list(_interruptible(frames(), stop_event)) == [b"frame-1", b"frame-2"]

    def test_passes_all_frames_when_the_event_is_never_set(self):
        stop_event = threading.Event()
        frames = [b"frame-1", b"frame-2", b"frame-3"]

        assert list(_interruptible(iter(frames), stop_event)) == frames

    def test_none_event_is_a_passthrough(self):
        frames = [b"frame-1", b"frame-2", b"frame-3"]

        assert list(_interruptible(iter(frames), None)) == frames

    def test_already_set_event_yields_nothing(self):
        stop_event = threading.Event()
        stop_event.set()
        frames = [b"frame-1", b"frame-2"]

        assert list(_interruptible(iter(frames), stop_event)) == []


class TestStreamingSilenceMs:
    """`config.streaming` selects which silence threshold the hands-free
    branch passes to `segment_stream`: `vad_silence_ms` when "off" (keeping
    `off` byte-identical to pre-streaming behavior), `streaming_pause_ms`
    when "sentence".
    """

    def _capture_kwargs(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_segment_stream(frames, vad, sample_rate, **kwargs):
            captured.update(kwargs)
            return iter([])

        monkeypatch.setattr("local_flow.audio.vad.segment_stream", fake_segment_stream)
        return captured

    def _run(self, config, monkeypatch, tmp_path):
        captured = self._capture_kwargs(monkeypatch)
        store = PersonalizationStore(tmp_path / "data")
        pipeline = _make_pipeline(store, MockChatClient(["x"]), FakeTextSink())
        reporter = FakeReporter()

        class DummySource:
            def frames(self, frame_ms):
                return iter([])

        _run_loop(
            config,
            "hands-free",
            reporter,
            dependencies=RunDependencies(pipeline, DummySource(), DummyVAD()),
        )
        return captured

    def test_off_passes_vad_silence_ms(self, monkeypatch, tmp_path):
        config = load_config(env={})
        captured = self._run(config, monkeypatch, tmp_path)
        assert captured["silence_ms"] == config.vad_silence_ms

    def test_sentence_passes_streaming_pause_ms(self, monkeypatch, tmp_path):
        config = load_config(
            env={
                "LOCAL_FLOW_STREAMING": "sentence",
                "LOCAL_FLOW_STREAMING_PAUSE_MS": "150",
            }
        )
        captured = self._run(config, monkeypatch, tmp_path)
        assert captured["silence_ms"] == 150
        assert captured["silence_ms"] != config.vad_silence_ms


class TestSentenceModeOrdering:
    """Sentence mode reuses hands-free's synchronous segment-then-handle
    loop, so the first chunk must fully insert before the second chunk is
    even transcribed -- there is no overlap/concurrency, just a shorter
    pause threshold. Verified end-to-end (real `segment_stream` + `EnergyVAD`,
    no monkeypatching) with fakes instrumented to log events in order.
    """

    def test_first_chunk_inserted_before_second_chunk_transcribed(self, tmp_path):
        event_log: list[tuple[str, str]] = []

        class LoggingTranscriber(MockTranscriber):
            def transcribe(self, pcm: bytes, sample_rate: int) -> str:
                event_log.append(("transcribe", repr(pcm)[:16]))
                return super().transcribe(pcm, sample_rate)

        class LoggingSink(FakeTextSink):
            def insert(self, text: str) -> None:
                event_log.append(("insert", text))
                super().insert(text)

        store = PersonalizationStore(tmp_path / "data")
        llm = MockChatClient(["First.", "Second."])
        pipeline = DictationPipeline(
            transcriber=LoggingTranscriber(["first chunk", "second chunk"]),
            polisher=TranscriptPolisher(llm, store),
            store=store,
            sink=LoggingSink(),
            command_mode=CommandMode(llm, dictionary_terms=store.dictionary_terms()),
        )
        reporter = FakeReporter()
        config = load_config(
            env={
                "LOCAL_FLOW_STREAMING": "sentence",
                "LOCAL_FLOW_STREAMING_PAUSE_MS": "300",
            }
        )

        # Two speech bursts separated by a pause well over streaming_pause_ms
        # (300ms), so `segment_stream` closes and yields the first chunk
        # before the second burst is ever seen.
        pcm = synth_pcm(
            [(200, 0), (600, 12000), (400, 0), (600, 12000), (200, 0)],
            sample_rate=config.sample_rate,
        )
        frame_bytes = int(config.sample_rate * config.vad_frame_ms / 1000) * 2
        frames = [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]

        class FakeSource:
            def frames(self, frame_ms):
                return iter(frames)

        vad = EnergyVAD(config.vad_energy_threshold)

        _run_loop(
            config,
            "hands-free",
            reporter,
            dependencies=RunDependencies(pipeline, FakeSource(), vad),
        )

        kinds = [kind for kind, _ in event_log]
        assert kinds == ["transcribe", "insert", "transcribe", "insert"]


class TestPushToTalkStreamingNotice:
    """Per the epic constraint, streaming applies to hands-free only:
    push-to-talk with `streaming != "off"` prints a one-line notice once and
    otherwise behaves exactly like `streaming="off"`.
    """

    def test_notice_printed_once_and_flow_matches_off(self, tmp_path, monkeypatch, capsys):
        store = PersonalizationStore(tmp_path / "data")
        llm = MockChatClient(["Send it."])
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["send it"]),
            polisher=TranscriptPolisher(llm, store),
            store=store,
            sink=sink,
            command_mode=CommandMode(llm, dictionary_terms=store.dictionary_terms()),
        )
        reporter = FakeReporter()
        config = load_config(env={"LOCAL_FLOW_STREAMING": "sentence"})
        done = threading.Event()

        class SignalingReporter(StatusReporter):
            def notify(self, state, detail: str = "") -> None:
                reporter.notify(state, detail)
                if state == "idle":
                    done.set()

        class FakeSource:
            def frames(self, frame_ms):
                return iter([])

            def record_until(self, stop, frame_ms):
                stop.wait(timeout=5)
                return b"pcm-bytes"

        class FakeListener:
            def run(self, on_press, on_release, on_cancel):
                on_press()
                on_release()
                done.wait(timeout=5)

        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: FakeListener(),
        )

        _run_loop(
            config,
            "push-to-talk",
            SignalingReporter(),
            dependencies=RunDependencies(pipeline, FakeSource(), DummyVAD()),
        )

        assert done.is_set(), "finish() never notified 'idle'"
        captured = capsys.readouterr()
        assert captured.out.count("streaming requires hands-free mode; ignoring") == 1
        assert [event[0] for event in reporter.events] == [
            "recording",
            "processing",
            "inserted",
            "idle",
        ]


class TestPushToTalkStopEvent:
    def test_stop_event_wakes_blocking_hotkey_listener(
        self, tmp_path, monkeypatch
    ):
        store = PersonalizationStore(tmp_path / "data")
        pipeline = _make_pipeline(store, MockChatClient(["unused"]), FakeTextSink())
        stopped = threading.Event()

        class FakeSource:
            def frames(self, frame_ms):
                return iter([])

        class BlockingListener:
            def run(self, on_press, on_release, on_cancel):
                assert stopped.wait(timeout=2), "stop() was never called"

            def stop(self):
                stopped.set()

        monkeypatch.setattr(
            "local_flow.hotkeys.base.create_hotkey_listener",
            lambda config, cancel_gate=None: BlockingListener(),
        )
        stop_event = threading.Event()
        stop_event.set()

        result = _run_loop(
            load_config(env={}),
            "push-to-talk",
            FakeReporter(),
            stop_event=stop_event,
            dependencies=RunDependencies(pipeline, FakeSource(), DummyVAD()),
        )

        assert result == 0
        assert stopped.is_set()


class TestLivePreviewRunLoop:
    """End-to-end: hands-free `_run_loop` with `streaming="live-preview"`,
    injecting a `MockStream` by monkeypatching `local_flow.app.
    _build_preview_stream` (the module-level factory `_run_loop` calls,
    rather than constructing a real `WindowedStream`). Uses the real
    `segment_stream` + `EnergyVAD` (no monkeypatching of those), same idiom
    as `TestSentenceModeOrdering`, so segment boundaries are genuine.
    """

    def _frames(self, config):
        # One utterance: leading silence, a long-enough speech burst for
        # several preview cadences, then silence past `vad_silence_ms`
        # (default 600ms) to close the segment.
        pcm = synth_pcm(
            [(200, 0), (900, 12000), (700, 0)],
            sample_rate=config.sample_rate,
        )
        frame_bytes = int(config.sample_rate * config.vad_frame_ms / 1000) * 2
        return [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]

    def _run(self, streaming, tmp_path, monkeypatch, preview_stream=None):
        store = PersonalizationStore(tmp_path / "data")
        llm = MockChatClient(["Final polished text."])
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["final rough transcript"]),
            polisher=TranscriptPolisher(llm, store),
            store=store,
            sink=sink,
            command_mode=CommandMode(llm, dictionary_terms=store.dictionary_terms()),
        )
        reporter = FakeReporter()
        config = load_config(env={"LOCAL_FLOW_STREAMING": streaming})

        if preview_stream is not None:
            monkeypatch.setattr(
                "local_flow.app._build_preview_stream",
                lambda transcriber, sample_rate: preview_stream,
            )

        frames = self._frames(config)

        class FakeSource:
            def frames(self, frame_ms):
                return iter(frames)

        vad = EnergyVAD(config.vad_energy_threshold)

        _run_loop(
            config,
            "hands-free",
            reporter,
            dependencies=RunDependencies(pipeline, FakeSource(), vad),
        )
        return reporter

    def test_preview_events_precede_processing_and_inserted(self, tmp_path, monkeypatch):
        preview_stream = MockStream(["rough", "rough one"], frames_per_partial=5)

        reporter = self._run("live-preview", tmp_path, monkeypatch, preview_stream)

        states = [event[0] for event in reporter.events]
        assert "preview" in states
        assert states.index("preview") < states.index("processing")
        assert states.index("preview") < states.index("inserted")
        preview_texts = [detail for state, detail in reporter.events if state == "preview"]
        assert preview_texts == ["rough", "rough one"]

    def test_final_inserted_text_matches_a_run_without_preview(self, tmp_path, monkeypatch):
        preview_stream = MockStream(["rough", "rough one"], frames_per_partial=5)

        with_preview = self._run("live-preview", tmp_path / "with", monkeypatch, preview_stream)
        without_preview = self._run("off", tmp_path / "without", monkeypatch, None)

        inserted_with = [d for s, d in with_preview.events if s == "inserted"]
        inserted_without = [d for s, d in without_preview.events if s == "inserted"]
        assert inserted_with == inserted_without == [repr("Final polished text.")]


class _RecordingPipeline:
    """Captures the PCM handed to `process_audio` and returns a scripted
    `DictationResult` -- used to verify `_handle_utterance`'s whisper-preset
    normalization and long-utterance warning wiring without a real ASR/LLM.
    """

    def __init__(self, duration_s: float = 0.0, final: str = "") -> None:
        self.received_pcm: bytes | None = None
        self._duration_s = duration_s
        self._final = final

    def process_audio(self, pcm: bytes, sample_rate: int) -> DictationResult:
        self.received_pcm = pcm
        return DictationResult(
            rough=self._final,
            cleaned=self._final,
            polished=self._final,
            final=self._final,
            duration_s=self._duration_s,
        )


class _FailingRecordingPipeline(_RecordingPipeline):
    """Like `_RecordingPipeline`, but raises after capturing the PCM -- used
    to inspect what was persisted to a `PendingAudioStore` before failure.
    """

    def process_audio(self, pcm: bytes, sample_rate: int) -> DictationResult:
        self.received_pcm = pcm
        raise LocalFlowError("simulated pipeline failure")


class TestHandleUtteranceNormalizeAudio:
    """`normalize_audio=True` (`vad_preset="whisper"`) peak-normalizes the
    PCM once, before both the pending-store save and `pipeline.process_audio`
    see it.
    """

    def _quiet_pcm(self) -> bytes:
        return array.array("h", [50, -80, 60, -40]).tobytes()

    def test_true_normalizes_pcm_before_processing(self):
        pipeline = _RecordingPipeline()
        reporter = FakeReporter()
        raw = self._quiet_pcm()

        _handle_utterance(pipeline, reporter, raw, 16000, normalize_audio=True)

        assert pipeline.received_pcm == normalize_peak(raw)
        assert pipeline.received_pcm != raw

    def test_false_leaves_pcm_unchanged(self):
        pipeline = _RecordingPipeline()
        reporter = FakeReporter()
        raw = self._quiet_pcm()

        _handle_utterance(pipeline, reporter, raw, 16000, normalize_audio=False)

        assert pipeline.received_pcm == raw

    def test_default_is_false(self):
        pipeline = _RecordingPipeline()
        reporter = FakeReporter()
        raw = self._quiet_pcm()

        _handle_utterance(pipeline, reporter, raw, 16000)

        assert pipeline.received_pcm == raw

    def test_pending_store_saves_the_normalized_bytes(self, tmp_path):
        from local_flow.audio.recovery import PendingAudioStore

        pending = PendingAudioStore(tmp_path)
        pipeline = _FailingRecordingPipeline()
        reporter = FakeReporter()
        raw = self._quiet_pcm()

        _handle_utterance(pipeline, reporter, raw, 16000, pending, normalize_audio=True)

        # Failure leaves the WAV in place (existing autosave contract); its
        # bytes should already be the normalized ones, not the raw input.
        [saved_path] = pending.pending()
        saved_pcm, saved_rate = pending.load(saved_path)
        assert saved_pcm == normalize_peak(raw)
        assert saved_rate == 16000


class TestHandleUtteranceLongUtteranceWarning:
    """After processing, a utterance longer than `max_utterance_min` minutes
    triggers an extra "warning" notification (informational only).
    """

    def test_over_threshold_triggers_warning(self):
        pipeline = _RecordingPipeline(duration_s=25 * 60, final="hello")
        reporter = FakeReporter()

        _handle_utterance(pipeline, reporter, b"pcm", 16000, max_utterance_min=20)

        warnings = [detail for state, detail in reporter.events if state == "warning"]
        assert len(warnings) == 1
        assert "25 minutes long" in warnings[0]
        assert "consider shorter dictations" in warnings[0]

    def test_under_threshold_no_warning(self):
        pipeline = _RecordingPipeline(duration_s=5 * 60, final="hello")
        reporter = FakeReporter()

        _handle_utterance(pipeline, reporter, b"pcm", 16000, max_utterance_min=20)

        assert not any(state == "warning" for state, _ in reporter.events)

    def test_exactly_at_threshold_no_warning(self):
        pipeline = _RecordingPipeline(duration_s=20 * 60, final="hello")
        reporter = FakeReporter()

        _handle_utterance(pipeline, reporter, b"pcm", 16000, max_utterance_min=20)

        assert not any(state == "warning" for state, _ in reporter.events)

    def test_default_max_utterance_min_is_twenty(self):
        pipeline = _RecordingPipeline(duration_s=21 * 60, final="hello")
        reporter = FakeReporter()

        _handle_utterance(pipeline, reporter, b"pcm", 16000)

        assert any(state == "warning" for state, _ in reporter.events)


class TestBuildVadWhisperPreset:
    """`_build_vad`'s `vad_preset="whisper"` threshold resolution -- see the
    docstring on `_build_vad` for the documented "explicit 500.0 counts as
    unset" limitation.
    """

    def test_whisper_preset_lowers_default_threshold(self):
        config = load_config(env={"LOCAL_FLOW_VAD_PRESET": "whisper"})
        vad = _build_vad(config)
        assert isinstance(vad, EnergyVAD)
        assert vad.threshold == 150.0

    def test_whisper_preset_does_not_override_an_explicit_non_default_threshold(self):
        config = load_config(
            env={
                "LOCAL_FLOW_VAD_PRESET": "whisper",
                "LOCAL_FLOW_VAD_ENERGY_THRESHOLD": "300",
            }
        )
        vad = _build_vad(config)
        assert vad.threshold == 300.0

    def test_normal_preset_leaves_threshold_untouched(self):
        config = load_config(
            env={"LOCAL_FLOW_VAD_PRESET": "normal", "LOCAL_FLOW_VAD_ENERGY_THRESHOLD": "700"}
        )
        vad = _build_vad(config)
        assert vad.threshold == 700.0

    def test_default_preset_is_normal_and_unaffected(self):
        config = load_config(env={})
        vad = _build_vad(config)
        assert vad.threshold == 500.0


class TestParseMicPriority:
    """`parse_mic_priority` mirrors `local_flow.tray.app.parse_languages`."""

    def test_splits_and_strips_comma_separated_entries(self):
        assert parse_mic_priority("AirPods, USB Mic") == ["AirPods", "USB Mic"]

    def test_empty_string_returns_empty_list(self):
        assert parse_mic_priority("") == []

    def test_blank_entries_are_dropped(self):
        assert parse_mic_priority("AirPods, , USB") == ["AirPods", "USB"]

    def test_case_insensitive_duplicates_removed_preserving_first_seen_order(self):
        assert parse_mic_priority("AirPods, airpods, USB") == ["AirPods", "USB"]

    def test_order_is_preserved(self):
        assert parse_mic_priority("c, a, b") == ["c", "a", "b"]


class TestBuildRunDependenciesWiring:
    """`_build_run_dependencies` threads `mic_priority`/`vad_preset`/
    `max_utterance_min` from `Config` into the `SounddeviceSource`
    construction and the returned `RunDependencies`.
    """

    def _config(self, tmp_path, **overrides):
        env = {
            "LOCAL_FLOW_DATA_DIR": str(tmp_path),
            "LOCAL_FLOW_ASR_BACKEND": "mock",
            "LOCAL_FLOW_LMSTUDIO_BASE_URL": "http://127.0.0.1:59999/v1",
            **overrides,
        }
        return load_config(env=env)

    def test_preferred_devices_and_whisper_and_max_utterance_are_wired(
        self, tmp_path, monkeypatch
    ):
        captured: dict[str, object] = {}

        class FakeSource:
            def __init__(self, sample_rate, device=None, preferred=None):
                captured["sample_rate"] = sample_rate
                captured["preferred"] = preferred

        monkeypatch.setattr("local_flow.audio.capture.SounddeviceSource", FakeSource)

        config = self._config(
            tmp_path,
            LOCAL_FLOW_MIC_PRIORITY="AirPods, USB",
            LOCAL_FLOW_VAD_PRESET="whisper",
            LOCAL_FLOW_MAX_UTTERANCE_MIN="5",
        )
        deps = _build_run_dependencies(config)

        assert captured["preferred"] == ["AirPods", "USB"]
        assert captured["sample_rate"] == config.sample_rate
        assert deps.normalize_audio is True
        assert deps.max_utterance_min == 5

    def test_normal_preset_and_default_max_utterance(self, tmp_path, monkeypatch):
        class FakeSource:
            def __init__(self, sample_rate, device=None, preferred=None):
                pass

        monkeypatch.setattr("local_flow.audio.capture.SounddeviceSource", FakeSource)

        config = self._config(tmp_path)
        deps = _build_run_dependencies(config)

        assert deps.normalize_audio is False
        assert deps.max_utterance_min == 20
