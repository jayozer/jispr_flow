"""Integration/smoke tests: the whole pipeline on mocks and fakes.

No microphone, GPU, downloaded model, or running LM Studio is needed.
"""

import json

import pytest

from local_flow.asr.mock import MockTranscriber
from local_flow.audio.vad import EnergyVAD
from local_flow.commands.command_mode import CommandMode
from local_flow.context.frontmost import AppInfo, MockFrontmostApp
from local_flow.context.router import ContextRouter
from local_flow.errors import LMStudioConnectionError
from local_flow.history.store import HistoryStore
from local_flow.insertion.base import FakeTextSink, InsertionManager
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import AppRule, PersonalizationStore
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


def make_pipeline(store, llm, sink, transcriber=None, history=None, router=None):
    return DictationPipeline(
        transcriber=transcriber or MockTranscriber(["placeholder"]),
        polisher=TranscriptPolisher(llm, store),
        store=store,
        sink=sink,
        command_mode=CommandMode(llm, dictionary_terms=store.dictionary_terms()),
        history=history,
        router=router,
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


class TestFailedFlagRecording:
    """`HistoryRecord.failed` (E11 carry-over binding): set only when a chat
    client is configured but never actually used, excluding
    cleanup_level="none" where the client is skipped by design (not a
    failure -- see `TranscriptPolisher.polish`).
    """

    def test_medium_level_raising_client_marks_failed_true(self, store, tmp_path):
        history = HistoryStore(tmp_path / "history")
        pipeline = make_pipeline(store, FailingChatClient(), FakeTextSink(), history=history)

        pipeline.process_transcript("email the postgresql team")

        assert history.recent()[0].used_llm is False
        assert history.recent()[0].failed is True

    def test_none_level_with_configured_client_leaves_failed_false(self, store, tmp_path):
        history = HistoryStore(tmp_path / "history")
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=TranscriptPolisher(FailingChatClient(), store, level="none"),
            store=store,
            sink=FakeTextSink(),
            history=history,
        )

        pipeline.process_transcript("hello world")

        assert history.recent()[0].used_llm is False
        assert history.recent()[0].failed is False

    def test_chat_client_none_leaves_failed_false(self, store, tmp_path):
        history = HistoryStore(tmp_path / "history")
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=TranscriptPolisher(None, store),  # not configured at all
            store=store,
            sink=FakeTextSink(),
            history=history,
        )

        pipeline.process_transcript("hello world")

        assert history.recent()[0].used_llm is False
        assert history.recent()[0].failed is False

    def test_successful_llm_use_leaves_failed_false(self, store, tmp_path):
        history = HistoryStore(tmp_path / "history")
        pipeline = make_pipeline(
            store, MockChatClient(["polished text"]), FakeTextSink(), history=history
        )

        pipeline.process_transcript("hello world")

        assert history.recent()[0].used_llm is True
        assert history.recent()[0].failed is False


class TestContextRoutingThroughPipeline:
    """The router is consulted once per utterance and affects style/sink/history."""

    def test_mapped_app_polishes_with_its_style(self, store):
        # "casual" is one of the built-in seeded styles (relaxed tone, no
        # greeting/sign-off); its rules text must reach the polish prompt.
        provider = MockFrontmostApp(AppInfo("com.tinyspeck.slackmacgap", "Slack"))
        router = ContextRouter(
            provider, {"com.tinyspeck.slackmacgap": AppRule(style="casual")}, {}
        )
        llm = MockChatClient(["ok"])
        pipeline = make_pipeline(store, llm, FakeTextSink(), router=router)

        pipeline.process_transcript("hey team quick update")

        casual_rules = store.style_rules("casual")[1]
        system = llm.requests[0][0]["content"]
        assert casual_rules in system

    def test_unmapped_app_polishes_with_default_style(self, store):
        provider = MockFrontmostApp(AppInfo("com.apple.mail", "Mail"))
        router = ContextRouter(
            provider, {"com.tinyspeck.slackmacgap": AppRule(style="casual")}, {}
        )
        llm = MockChatClient(["ok"])
        pipeline = make_pipeline(store, llm, FakeTextSink(), router=router)

        pipeline.process_transcript("hey team quick update")

        casual_rules = store.style_rules("casual")[1]
        default_rules = store.style_rules("default")[1]
        system = llm.requests[0][0]["content"]
        assert casual_rules not in system
        assert default_rules in system

    def test_per_app_insert_routes_to_the_configured_sink(self, store):
        provider = MockFrontmostApp(AppInfo("claude", "Claude Code"))
        type_sink = FakeTextSink()
        router = ContextRouter(
            provider, {"claude": AppRule(insert="type")}, {"type": type_sink}
        )
        default_sink = FakeTextSink()
        llm = MockChatClient(["polished text"])
        pipeline = make_pipeline(store, llm, default_sink, router=router)

        pipeline.process_transcript("hello world")

        assert type_sink.events
        assert default_sink.events == []

    def test_history_records_app_id_resolved_by_router(self, store, tmp_path):
        history = HistoryStore(tmp_path / "history")
        provider = MockFrontmostApp(AppInfo("com.tinyspeck.slackmacgap", "Slack"))
        router = ContextRouter(provider, {}, {})
        llm = MockChatClient(["hello"])
        pipeline = make_pipeline(store, llm, FakeTextSink(), history=history, router=router)

        pipeline.process_transcript("hello")

        records = history.recent()
        assert len(records) == 1
        assert records[0].app == "com.tinyspeck.slackmacgap"

    def test_router_none_is_byte_identical_to_no_routing(self, store, tmp_path):
        """Guards the `router=None` default: same style, same sink, `app=""`."""
        history = HistoryStore(tmp_path / "history")
        llm = MockChatClient(["ok"])
        sink = FakeTextSink()
        pipeline = make_pipeline(store, llm, sink, history=history, router=None)

        pipeline.process_transcript("hello world")

        assert sink.events == [("insert", "ok")]
        assert history.recent()[0].app == ""


class TestSinkOverride:
    """`sink_override` (Phase 7 E13 scratchpad): wins over BOTH the router's
    per-app sink and the pipeline's own configured sink -- this is what lets
    the scratchpad dictate-to-pad hotkey force insertion into the active note
    regardless of which app is frontmost. Style/app_id resolution (`ctx`) is
    untouched either way.
    """

    def test_override_wins_over_per_app_router_sink(self, store):
        provider = MockFrontmostApp(AppInfo("claude", "Claude Code"))
        type_sink = FakeTextSink()
        router = ContextRouter(
            provider, {"claude": AppRule(insert="type")}, {"type": type_sink}
        )
        default_sink = FakeTextSink()
        override_sink = FakeTextSink()
        llm = MockChatClient(["polished text"])
        pipeline = make_pipeline(store, llm, default_sink, router=router)

        pipeline.process_transcript("hello world", sink_override=override_sink)

        assert override_sink.events == [("insert", "polished text")]
        assert type_sink.events == []
        assert default_sink.events == []

    def test_override_wins_over_plain_default_sink_when_no_router(self, store):
        default_sink = FakeTextSink()
        override_sink = FakeTextSink()
        llm = MockChatClient(["ok"])
        pipeline = make_pipeline(store, llm, default_sink, router=None)

        pipeline.process_transcript("hello world", sink_override=override_sink)

        assert override_sink.events == [("insert", "ok")]
        assert default_sink.events == []

    def test_history_app_id_and_style_are_unaffected_by_override(self, store, tmp_path):
        history = HistoryStore(tmp_path / "history")
        provider = MockFrontmostApp(AppInfo("com.tinyspeck.slackmacgap", "Slack"))
        router = ContextRouter(
            provider, {"com.tinyspeck.slackmacgap": AppRule(style="casual")}, {}
        )
        default_sink = FakeTextSink()
        override_sink = FakeTextSink()
        llm = MockChatClient(["ok"])
        pipeline = make_pipeline(
            store, llm, default_sink, history=history, router=router
        )

        pipeline.process_transcript("hey team quick update", sink_override=override_sink)

        casual_rules = store.style_rules("casual")[1]
        system = llm.requests[0][0]["content"]
        assert casual_rules in system  # style override still applied
        assert history.recent()[0].app == "com.tinyspeck.slackmacgap"
        assert override_sink.events  # insertion still went to the override
        assert default_sink.events == []

    def test_none_is_byte_identical_to_before_the_parameter_existed(self, store):
        sink = FakeTextSink()
        llm = MockChatClient(["ok"])
        pipeline = make_pipeline(store, llm, sink, router=None)

        pipeline.process_transcript("hello world", sink_override=None)

        assert sink.events == [("insert", "ok")]

    def test_process_audio_threads_override_through_to_process_transcript(self, store):
        from local_flow.asr.mock import MockTranscriber

        default_sink = FakeTextSink()
        override_sink = FakeTextSink()
        llm = MockChatClient(["ok"])
        pipeline = make_pipeline(
            store, llm, default_sink, transcriber=MockTranscriber(["hi there"])
        )

        pipeline.process_audio(b"pcm-bytes", 16000, sink_override=override_sink)

        assert override_sink.events == [("insert", "ok")]
        assert default_sink.events == []


class TestSpokenDictionaryAddition:
    """"add X to [the] dictionary" is pure rules; works with LM Studio down."""

    def test_add_to_dictionary_with_llm_absent_adds_term_and_warns(self, tmp_path):
        store = PersonalizationStore(tmp_path / "data")
        polisher = TranscriptPolisher(None, store)  # chat_client=None: rules only
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=polisher,
            store=store,
            sink=sink,
        )

        result = pipeline.process_transcript("add JiSpr to dictionary")

        assert result.used_llm is False
        assert result.final == ""
        assert result.inserted is False
        assert "JiSpr" in store.dictionary_terms()
        assert any("added 'JiSpr' to dictionary" in w for w in result.warnings)

    def test_add_to_dictionary_when_already_present_notes_duplicate(self, tmp_path):
        store = PersonalizationStore(tmp_path / "data")
        store.add_dictionary_term("JiSpr")
        polisher = TranscriptPolisher(None, store)
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=polisher,
            store=store,
            sink=sink,
        )

        result = pipeline.process_transcript("add JiSpr to dictionary")

        assert any("'JiSpr' already in dictionary" in w for w in result.warnings)


class TestAutoTransform:
    """`auto_transform_prompt` (Phase 6 E8): applied to the final text right
    before insertion, after personalization (dictionary/snippets/dictation
    commands), skipped at cleanup_level="none" or with no chat client, and a
    complete no-op when unset (the default) -- see `local_flow.pipeline`.
    """

    def test_default_is_none_and_byte_identical_to_before_the_feature(self, store):
        llm = MockChatClient(["polished text"])
        sink = FakeTextSink()
        pipeline = make_pipeline(store, llm, sink)
        assert pipeline.auto_transform_prompt is None

        result = pipeline.process_transcript("hello world")

        assert result.final == "polished text"
        assert len(llm.requests) == 1  # only the polish call, no transform call
        assert sink.events == [("insert", "polished text")]

    def test_applied_after_personalization_and_before_insertion(self, store):
        # The polish call returns text that still needs dictionary/snippet
        # substitution; the auto-transform call must see the *substituted*
        # text, not the raw polish output.
        llm = MockChatClient(
            [
                "the jispr flow team uses postgresql daily",  # polish output (lowercase)
                "TRANSFORMED",  # auto-transform output
            ]
        )
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=TranscriptPolisher(llm, store),
            store=store,
            sink=sink,
            auto_transform_prompt="Rewrite for clarity.",
        )

        result = pipeline.process_transcript("some rough words")

        # The transform call's user message must contain the *personalized*
        # text (dictionary-cased terms), proving ordering: personalization
        # ran first, auto-transform second.
        transform_request = llm.requests[1]
        assert "JiSpr Flow" in transform_request[1]["content"]
        assert "PostgreSQL" in transform_request[1]["content"]
        assert result.final == "TRANSFORMED"
        assert sink.events == [("insert", "TRANSFORMED")]

    def test_llm_failure_degrades_to_original_text_with_a_warning(self, store):
        class FailOnSecondCall(MockChatClient):
            def __init__(self):
                super().__init__(["polished text"])
                self.calls = 0

            def chat(self, messages, *, temperature=0.2, max_tokens=None):
                self.calls += 1
                if self.calls > 1:
                    raise LMStudioConnectionError("LM Studio is unreachable")
                return super().chat(messages)

        llm = FailOnSecondCall()
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=TranscriptPolisher(llm, store),
            store=store,
            sink=sink,
            auto_transform_prompt="Rewrite for clarity.",
        )

        result = pipeline.process_transcript("hello world")

        assert result.final == "polished text"  # original text, not lost
        assert any("auto-transform skipped" in w for w in result.warnings)
        assert sink.events == [("insert", "polished text")]

    def test_skipped_at_cleanup_level_none(self, store):
        llm = MockChatClient(["should never be called for transform"])
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=TranscriptPolisher(llm, store, level="none"),
            store=store,
            sink=sink,
            auto_transform_prompt="Rewrite for clarity.",
        )

        result = pipeline.process_transcript("hello world")

        assert len(llm.requests) == 0  # polish itself is also skipped at "none"
        assert result.final == "hello world"

    def test_skipped_when_no_chat_client_configured(self, store):
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=TranscriptPolisher(None, store),
            store=store,
            sink=sink,
            auto_transform_prompt="Rewrite for clarity.",
        )

        result = pipeline.process_transcript("hello world")

        assert result.final  # rule-cleaned text still inserted
        assert sink.events[0][0] == "insert"

    def test_empty_transform_output_keeps_original_text_with_a_warning(self, store):
        # A whitespace-only completion must not silently discard the whole
        # utterance -- same guard as TranscriptPolisher's `if polished:`:
        # keep the untransformed text, insert it, and warn.
        llm = MockChatClient(["polished text", "   "])  # polish, then transform
        sink = FakeTextSink()
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=TranscriptPolisher(llm, store),
            store=store,
            sink=sink,
            auto_transform_prompt="Rewrite for clarity.",
        )

        result = pipeline.process_transcript("hello world")

        assert result.final == "polished text"  # original text, not lost
        assert sink.events == [("insert", "polished text")]
        assert any("auto-transform returned no text" in w for w in result.warnings)

    def test_empty_final_text_never_calls_the_transform(self, store):
        # Chat client IS configured (unlike the previous test) and level is
        # the default "medium" -- but the polish response is entirely a
        # spoken "add X to dictionary" phrase, which `extract_dictionary_additions`
        # strips down to "". The `and text` guard must still skip the
        # transform call even though a chat client exists.
        llm = MockChatClient(["add JiSpr to dictionary"])
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(["placeholder"]),
            polisher=TranscriptPolisher(llm, store),
            store=store,
            sink=FakeTextSink(),
            auto_transform_prompt="Rewrite for clarity.",
        )

        result = pipeline.process_transcript("add JiSpr to dictionary")

        assert result.final == ""
        assert len(llm.requests) == 1  # only the polish call, no transform call


class TestDictionaryUsageTracking:
    """process_transcript records per-term usage back into the dictionary."""

    def test_two_enforced_terms_update_uses_in_dictionary_json(self, store, tmp_path):
        llm = MockChatClient(
            ["The JiSpr Flow rollout uses PostgreSQL and PostgreSQL again."]
        )
        sink = FakeTextSink()
        pipeline = make_pipeline(store, llm, sink)

        pipeline.process_transcript("some rough words")

        on_disk = json.loads((tmp_path / "data" / "dictionary.json").read_text())
        entries = {e["term"]: e for e in on_disk["terms"] if isinstance(e, dict)}
        assert entries["JiSpr Flow"]["uses"] == 1
        assert entries["PostgreSQL"]["uses"] == 2

    def test_no_dictionary_terms_matched_leaves_dictionary_untouched(self, store, tmp_path):
        llm = MockChatClient(["nothing special here"])
        sink = FakeTextSink()
        pipeline = make_pipeline(store, llm, sink)

        pipeline.process_transcript("some rough words")

        on_disk = json.loads((tmp_path / "data" / "dictionary.json").read_text())
        assert on_disk["terms"] == ["JiSpr Flow", "PostgreSQL"]
