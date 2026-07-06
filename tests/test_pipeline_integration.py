"""Integration/smoke tests: the whole pipeline on mocks and fakes.

No microphone, GPU, downloaded model, or running LM Studio is needed.
"""

import pytest

from local_flow.asr.mock import MockTranscriber
from local_flow.audio.vad import EnergyVAD
from local_flow.commands.command_mode import CommandMode
from local_flow.errors import LMStudioConnectionError
from local_flow.history.store import HistoryStore
from local_flow.insertion.base import FakeTextSink, InsertionManager
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline
from local_flow.polish.polisher import TranscriptPolisher


class FailingChatClient(MockChatClient):
    """Simulates LM Studio being down."""

    def chat(self, messages, *, temperature=0.2, max_tokens=None):
        raise LMStudioConnectionError(
            "Could not reach LM Studio at http://localhost:1234/v1: refused"
        )


@pytest.fixture
def store(tmp_path):
    store = PersonalizationStore(tmp_path / "data")
    store.add_dictionary_term("JiSpr Flow")
    store.add_dictionary_term("PostgreSQL")
    store.set_snippet("sig block", "Best regards,\nJay")
    return store


def make_pipeline(store, llm, sink, transcriber=None, history=None):
    return DictationPipeline(
        transcriber=transcriber or MockTranscriber(["placeholder"]),
        polisher=TranscriptPolisher(llm, store),
        store=store,
        sink=sink,
        command_mode=CommandMode(llm, dictionary_terms=store.dictionary_terms()),
        history=history,
    )


class TestTranscriptToInsertion:
    def test_full_text_pipeline(self, store):
        llm = MockChatClient(
            ["The JiSpr Flow rollout is on track. Add sig block press enter"]
        )
        sink = FakeTextSink()
        pipeline = make_pipeline(store, llm, sink)

        rough = "um the jispr flow rollout is uh on track, add sig block press enter"
        result = pipeline.process_transcript(rough)

        assert result.used_llm is True
        assert "um" not in result.cleaned.split()
        assert "JiSpr Flow" in result.final
        assert "Best regards,\nJay" in result.final
        assert result.actions == ["enter"]
        assert sink.events[0][0] == "insert"
        assert sink.events[-1] == ("key", "enter")
        assert pipeline.last_transcript == result.final

    def test_polish_prompt_contains_dictionary_and_style(self, store):
        llm = MockChatClient(["ok"])
        pipeline = make_pipeline(store, llm, FakeTextSink())
        pipeline.process_transcript("hello world")
        system = llm.requests[0][0]["content"]
        assert "JiSpr Flow" in system
        assert "PostgreSQL" in system

    def test_empty_transcript_inserts_nothing(self, store):
        sink = FakeTextSink()
        pipeline = make_pipeline(store, MockChatClient(["should not be used"]), sink)
        result = pipeline.process_transcript("")
        assert result.inserted is False
        assert sink.events == []


class TestAudioToInsertion:
    def test_audio_is_segmented_and_transcribed(self, store):
        from local_flow.demo import synth_pcm

        transcriber = MockTranscriber(["first part.", "second part."])
        llm = MockChatClient(["First part. Second part."])
        sink = FakeTextSink()
        pipeline = make_pipeline(store, llm, sink, transcriber=transcriber)

        pcm = synth_pcm([(150, 0), (600, 12000), (800, 0), (600, 12000), (150, 0)])
        result = pipeline.process_audio(pcm, 16000, vad=EnergyVAD(500), silence_ms=400)

        assert len(transcriber.calls) == 2
        assert result.rough == "first part. second part."
        assert sink.text.startswith("First part.")


class TestLMStudioDownFallback:
    def test_rules_still_apply_and_warning_is_surfaced(self, store):
        sink = FakeTextSink()
        pipeline = make_pipeline(store, FailingChatClient(), sink)
        result = pipeline.process_transcript(
            "um email bob, scratch that, email the postgresql team"
        )
        assert result.used_llm is False
        assert result.final == "email the PostgreSQL team"
        assert any("LM Studio polish skipped" in w for w in result.warnings)
        assert sink.events == [("insert", "email the PostgreSQL team")]


class TestCommandModeThroughPipeline:
    def test_transforms_last_transcript_and_inserts(self, store):
        llm = MockChatClient(["polished dictation", "- bullet one\n- bullet two"])
        sink = FakeTextSink()
        pipeline = make_pipeline(store, llm, sink)
        pipeline.process_transcript("some rough words")
        transformed = pipeline.run_command("turn it into bullets")
        assert transformed == "- bullet one\n- bullet two"
        assert sink.events[-1] == ("insert", "- bullet one\n- bullet two")

    def test_insertion_fallback_inside_pipeline(self, store):
        from tests.test_insertion import BoomSink

        fallback = FakeTextSink()
        sink = InsertionManager([BoomSink(), fallback])
        pipeline = make_pipeline(store, MockChatClient(["clean text"]), sink)
        pipeline.process_transcript("some words")
        assert fallback.events == [("insert", "clean text")]


class TestHistoryRecording:
    def test_records_entry_with_duration_and_replacements(self, store, tmp_path):
        history = HistoryStore(tmp_path / "history")
        llm = MockChatClient(["The JiSpr Flow rollout needs sig block."])
        sink = FakeTextSink()
        pipeline = make_pipeline(store, llm, sink, history=history)

        rough = "the jispr flow rollout needs sig block"
        result = pipeline.process_transcript(rough, duration_s=2.5)

        records = history.recent()
        assert len(records) == 1
        record = records[0]
        assert record.rough == rough
        assert record.final == result.final
        assert record.used_llm is True
        assert record.duration_s == 2.5
        # 1 dictionary substitution (JiSpr Flow) + 1 snippet substitution (sig block)
        assert record.replacements == 2

    def test_history_none_records_nothing(self, store, tmp_path):
        sink = FakeTextSink()
        pipeline = make_pipeline(store, MockChatClient(["hello"]), sink, history=None)
        pipeline.process_transcript("some rough text")
        # Nothing should have been written where a history store would live.
        assert HistoryStore(tmp_path / "history").recent() == []

    def test_empty_rough_records_nothing(self, store, tmp_path):
        history = HistoryStore(tmp_path / "history")
        sink = FakeTextSink()
        pipeline = make_pipeline(
            store, MockChatClient(["should not be used"]), sink, history=history
        )
        result = pipeline.process_transcript("")
        assert result.inserted is False
        assert history.recent() == []

    def test_duration_computed_from_transcribed_pcm_bytes(self, store, tmp_path):
        from local_flow.demo import synth_pcm

        history = HistoryStore(tmp_path / "history")
        transcriber = MockTranscriber(["first part.", "second part."])
        llm = MockChatClient(["First part. Second part."])
        sink = FakeTextSink()
        pipeline = make_pipeline(store, llm, sink, transcriber=transcriber, history=history)

        pcm = synth_pcm([(150, 0), (600, 12000), (800, 0), (600, 12000), (150, 0)])
        pipeline.process_audio(pcm, 16000, vad=EnergyVAD(500), silence_ms=400)

        expected_duration = sum(length for length, _rate in transcriber.calls) / (2 * 16000)
        records = history.recent()
        assert len(records) == 1
        assert records[0].duration_s == pytest.approx(expected_duration)
        assert expected_duration > 0
