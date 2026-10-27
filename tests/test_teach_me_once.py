"""Teach Me Once across the native bridge and a headless dictation pipeline."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from local_flow.app_host import AppHost
from local_flow.asr.mock import MockTranscriber
from local_flow.context.frontmost import AppInfo, MockFrontmostApp
from local_flow.errors import ConfigError
from local_flow.insertion.base import FakeTextSink
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.corrections import CorrectionStore
from local_flow.personalization.review import MAX_RECENT_REVIEWS, MAX_REVIEW_CHARS, DictationReviews
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline, DictationResult
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.settings.controller import SettingsController
from local_flow.settings.service import SettingsService


@pytest.fixture
def setup(tmp_path):
    config_path = tmp_path / "config.toml"
    data_dir = tmp_path / "data"
    config_path.write_text(f'data_dir = "{data_dir}"\nhistory_enabled = false\n')
    controller = SettingsController(service=SettingsService(
        env={"LOCAL_FLOW_CONFIG": str(config_path)}
    ))
    controller.load()  # Initialize only the existing personalization files.
    output = io.StringIO()
    host = AppHost(input_stream=io.StringIO(), output_stream=output, controller=controller)
    store = PersonalizationStore(data_dir)
    corrections = CorrectionStore(data_dir)
    frontmost = MockFrontmostApp(AppInfo("com.example.editor", "Example Editor"))
    sink = FakeTextSink()
    pipeline = DictationPipeline(
        MockTranscriber([]), TranscriptPolisher(None, store), store, sink,
        corrections=corrections, frontmost=frontmost,
        on_dictation=host.reporter.dictation_completed,
    )
    return SimpleNamespace(
        host=host, output=output, store=store, corrections=corrections,
        frontmost=frontmost, sink=sink, pipeline=pipeline, config_path=config_path,
    )


def command(setup, command, **payload):
    request_id = f"request-{len(setup.output.getvalue())}"
    setup.host.handle({"v": 1, "id": request_id, "command": command, "payload": payload})
    messages = [json.loads(line) for line in setup.output.getvalue().splitlines()]
    return next(message["result"] for message in reversed(messages)
                if message.get("id") == request_id)


def prepare_review(setup):
    setup.pipeline.process_transcript("We utilize this approach.")
    return command(setup, "review_latest")["dictation"]


def learn(setup, review, *, scope="app"):
    return command(
        setup, "correction_learn", dictation_id=review["id"],
        corrected_text="We use this approach.", source="utilize", replacement="use", scope=scope,
    )


def test_review_and_learning_work_without_saved_history_or_style_rules(setup):
    review = prepare_review(setup)
    assert review["text"] == "We utilize this approach."
    assert review["app_id"] == "com.example.editor"
    assert review["app_name"] == "Example Editor"
    assert setup.pipeline.router is None
    assert setup.pipeline.history is None
    preview = command(
        setup, "correction_preview", dictation_id=review["id"],
        corrected_text="We use this approach.",
    )
    assert preview == {
        "dictation_id": review["id"],
        "suggestions": [{"source": "utilize", "replacement": "use"}],
    }
    learned = learn(setup, review)
    assert learned["changed"] is True
    assert learned["undo_token"]
    assert learned["rule"]["app_id"] == "com.example.editor"
    result = setup.pipeline.process_transcript("We utilize this approach.")
    assert result.final == "We use this approach."
    assert result.corrections_applied == 1
    assert command(setup, "review_latest")["dictation"]["corrections_applied"] == 1


def test_scope_does_not_leak_to_another_app_and_global_is_explicit(setup):
    review = prepare_review(setup)
    learn(setup, review)
    setup.frontmost.info = AppInfo("com.example.other", "Other App")
    assert setup.pipeline.process_transcript("We utilize this approach.").final == review["text"]
    global_result = learn(setup, review, scope="global")
    assert global_result["rule"]["app_id"] == ""
    assert setup.pipeline.process_transcript(review["text"]).final == "We use this approach."


def test_learning_changes_only_its_separate_file_and_no_config_keys(setup):
    baseline = {path.name: path.read_bytes() for path in setup.store.data_dir.iterdir()}
    config_before = setup.config_path.read_bytes()
    review = prepare_review(setup)
    learn(setup, review)
    for name, content in baseline.items():
        assert (setup.store.data_dir / name).read_bytes() == content
    assert setup.config_path.read_bytes() == config_before
    assert set(path.name for path in setup.store.data_dir.iterdir()) == {
        *baseline, "corrections.json"
    }


def test_undo_is_single_use_and_restores_future_dictation(setup):
    review = prepare_review(setup)
    token = learn(setup, review)["undo_token"]
    assert command(setup, "correction_undo", token=token) == {"changed": True}
    assert setup.pipeline.process_transcript(review["text"]).final == review["text"]
    with pytest.raises(ConfigError, match="no longer available"):
        command(setup, "correction_undo", token=token)


def test_undo_preserves_a_rule_changed_elsewhere(setup):
    review = prepare_review(setup)
    token = learn(setup, review)["undo_token"]
    setup.corrections.learn("utilize", "employ", app_id=review["app_id"])
    with pytest.raises(ConfigError, match="has changed"):
        command(setup, "correction_undo", token=token)
    assert setup.corrections.apply("utilize", review["app_id"])[0] == "employ"


def test_forget_and_undo_forget_restore_the_same_rule(setup):
    review = prepare_review(setup)
    rule = learn(setup, review)["rule"]
    forgotten = command(setup, "correction_forget", rule_id=rule["id"])
    assert setup.corrections.list_rules() == []
    command(setup, "correction_undo", token=forgotten["undo_token"])
    assert setup.corrections.list_rules()[0].id == rule["id"]


@pytest.mark.parametrize("changes", [
    {"dictation_id": "fabricated"},
    {"source": "invented"},
    {"replacement": "invented"},
    {"scope": "project"},
    {"corrected_text": "x" * (MAX_REVIEW_CHARS + 1)},
    {"corrected_text": None},
])
def test_bridge_rejects_unreviewed_or_invalid_learning(setup, changes):
    review = prepare_review(setup)
    payload = {
        "dictation_id": review["id"], "corrected_text": "We use this approach.",
        "source": "utilize", "replacement": "use", "scope": "app", **changes,
    }
    with pytest.raises((ConfigError, ValueError)):
        command(setup, "correction_learn", **payload)
    assert not setup.corrections.path.exists()


def test_unknown_origin_cannot_create_an_app_scoped_rule(setup):
    setup.frontmost.info = AppInfo()
    review = prepare_review(setup)
    with pytest.raises(ConfigError, match="no app identity"):
        learn(setup, review)
    assert learn(setup, review, scope="global")["changed"] is True


def test_review_cache_is_bounded_and_evicted_ids_are_rejected():
    reviews = DictationReviews()
    result = DictationResult("one", "one", "one", "one", inserted=True)
    first = reviews.add(result)
    for _ in range(MAX_RECENT_REVIEWS):
        latest = reviews.add(result)
    assert reviews.latest() == latest
    with pytest.raises(ConfigError, match="no longer available"):
        reviews.get(first.id)
    assert reviews.add(DictationResult("", "", "", "")) is None
    assert reviews.latest() is None


def test_app_is_resolved_after_polish_if_focus_changes(setup):
    setup.corrections.learn("utilize", "employ", app_id="com.example.other")

    def polish(_messages):
        setup.frontmost.info = AppInfo("com.example.other", "Other App")
        return "We utilize this approach."

    setup.pipeline.polisher = TranscriptPolisher(MockChatClient(polish), setup.store)
    result = setup.pipeline.process_transcript("rough input")
    assert result.final == "We employ this approach."
    assert command(setup, "review_latest")["dictation"]["app_id"] == "com.example.other"


def test_learned_output_is_literal_and_cannot_trigger_commands(setup):
    setup.corrections.learn("utilize", "press enter")
    result = setup.pipeline.process_transcript("We utilize this approach.")
    assert result.final == "We press enter this approach."
    assert result.actions == []
    assert setup.sink.events == [("insert", result.final)]


def test_verbatim_cleanup_skips_learned_replacements(setup):
    setup.corrections.learn("utilize", "use")
    setup.pipeline.polisher = TranscriptPolisher(None, setup.store, level="none")
    assert setup.pipeline.process_transcript("We utilize this.").final == "We utilize this."


def test_corrupt_rules_do_not_break_dictation_or_host_snapshot(setup):
    setup.corrections.path.write_text("not json")
    result = setup.pipeline.process_transcript("We utilize this approach.")
    assert result.final == "We utilize this approach."
    assert any("learned corrections unavailable" in warning for warning in result.warnings)
    snapshot = setup.host.snapshot_payload()
    assert snapshot["corrections"] == []
    assert snapshot["corrections_error"]
    assert setup.corrections.path.read_text() == "not json"


def test_review_callback_failure_never_retries_or_discards_inserted_text(setup):
    def fail(_result):
        raise OSError("review unavailable")

    setup.pipeline.on_dictation = fail
    result = setup.pipeline.process_transcript("hello")
    assert setup.sink.events == [("insert", "hello")]
    assert result.inserted
    assert any("review could not be updated" in warning for warning in result.warnings)


def test_review_arrives_before_history_failure_after_successful_insertion(setup):
    def fail(**_kwargs):
        raise OSError("history disk full")

    setup.pipeline.history = SimpleNamespace(append_new=fail)
    with pytest.raises(OSError, match="history disk full"):
        setup.pipeline.process_transcript("hello")
    assert setup.sink.events == [("insert", "hello")]
    assert command(setup, "review_latest")["dictation"]["text"] == "hello"


def test_review_arrives_when_text_succeeds_but_trailing_key_fails(setup):
    class KeyFailure(FakeTextSink):
        def press_key(self, _key):
            raise OSError("key failed")

    sink = KeyFailure()
    setup.pipeline.sink = sink
    with pytest.raises(OSError, match="key failed"):
        setup.pipeline.process_transcript("hello press enter")
    assert sink.events == [("insert", "hello")]
    assert command(setup, "review_latest")["dictation"]["text"] == "hello"


def test_failed_text_insertion_produces_no_review(setup):
    def fail(_text):
        raise OSError("insertion failed")

    setup.pipeline.sink.insert = fail
    with pytest.raises(OSError):
        setup.pipeline.process_transcript("hello")
    assert command(setup, "review_latest")["dictation"] is None


def test_cli_without_rules_or_review_does_not_query_the_frontmost_app(setup):
    def fail():
        raise AssertionError("unnecessary platform lookup")

    setup.pipeline.on_dictation = None
    setup.frontmost.current = fail
    assert setup.pipeline.process_transcript("hello").final == "hello"


def test_scratchpad_override_uses_global_rules_and_is_not_sent_to_review(setup):
    setup.corrections.learn("utilize", "use")
    setup.corrections.learn("utilize", "employ", app_id="com.example.editor")
    override = FakeTextSink()
    result = setup.pipeline.process_transcript("We utilize this.", sink_override=override)
    assert result.final == "We use this."
    assert command(setup, "review_latest")["dictation"] is None
