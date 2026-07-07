"""Tests for the StatusReporter seam: ConsoleReporter output mapping and the
extracted per-utterance handler in ``local_flow.app``.
"""

from local_flow.app import _handle_utterance
from local_flow.asr.mock import MockTranscriber
from local_flow.commands.command_mode import CommandMode
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
