"""Tests for the StatusReporter seam: ConsoleReporter output mapping and the
extracted per-utterance handler in ``local_flow.app``.
"""

import threading

from local_flow.app import _handle_utterance, _interruptible, _run_loop
from local_flow.asr.mock import MockTranscriber
from local_flow.audio.vad import EnergyVAD
from local_flow.commands.command_mode import CommandMode
from local_flow.config import load_config
from local_flow.demo import synth_pcm
from local_flow.errors import LMStudioConnectionError
from local_flow.insertion.base import FakeTextSink
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline
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
            dependencies=(pipeline, DummySource(), DummyVAD()),
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
            lambda config: FakeListener(),
        )

        _run_loop(
            config,
            "push-to-talk",
            SignalingReporter(),
            dependencies=(pipeline, FakeSource(), DummyVAD()),
        )

        assert done.is_set(), "cancel() never notified 'idle'"
        assert reporter.events[-1] == ("idle", "")
        assert "dictation discarded" in capsys.readouterr().out


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
            dependencies=(pipeline, DummySource(), DummyVAD()),
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
            dependencies=(pipeline, FakeSource(), vad),
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
            lambda config: FakeListener(),
        )

        _run_loop(
            config,
            "push-to-talk",
            SignalingReporter(),
            dependencies=(pipeline, FakeSource(), DummyVAD()),
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
