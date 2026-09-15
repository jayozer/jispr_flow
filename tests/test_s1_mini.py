"""S1-mini protocol, safe insertion, and coexistence through mocked HTTP."""

import json

import httpx
import pytest

from local_flow.asr.mock import MockTranscriber
from local_flow.commands.command_mode import CommandMode
from local_flow.context.field_text import FieldContext
from local_flow.errors import LMStudioError, LMStudioModelError
from local_flow.insertion.base import FakeTextSink
from local_flow.llm.lmstudio import LMStudioClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.polish.s1_mini import MAX_CHUNK_BYTES, SYSTEM_PROMPT, is_s1_mini
from local_flow.transforms.registry import apply_transform


def completion(text="Clean text.", finish="stop"):
    return httpx.Response(200, json={"choices": [{"text": text, "finish_reason": finish}]})


def setup(tmp_path, handler=None, model="s1-mini", **kwargs):
    requests = []

    def transport(request):
        requests.append(request)
        return handler(request) if handler else completion()

    client = LMStudioClient(model=model, transport=httpx.MockTransport(transport))
    store = PersonalizationStore(tmp_path)
    return TranscriptPolisher(client, store, **kwargs), requests


@pytest.mark.parametrize("model", [
    "s1-mini", "superwhisper/s1-mini", "superwhisper/s1-mini-GGUF",
    "s1-mini-q4_k_m.gguf", "S1-mini:2", "superwhisper/s1-mini@q4_k_m",
])
def test_recognizes_standard_model_identifiers(model):
    assert is_s1_mini(model)


@pytest.mark.parametrize("model", ["gemma-4-12b-it-mlx", "s1", "s1-minimum", "not-s1-mini"])
def test_leaves_other_models_alone(model):
    assert not is_s1_mini(model)


def test_exact_raw_protocol_keeps_correction_context_and_omits_field_text(tmp_path):
    polisher, requests = setup(tmp_path, lambda _: completion("Send the report by Thursday."))
    raw = "um send the report by friday, no wait make that thursday"
    result = polisher.polish(raw, field_context=FieldContext(before_cursor="PRIVATE CONTEXT"))
    assert result.polished == "Send the report by Thursday."
    assert result.used_llm
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/v1/completions"
    payload = json.loads(request.content)
    assert payload["prompt"] == (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        "<|im_start|>user\n[Styling: semi-formal] [Structure: lists] [Context: general]\n"
        f"{raw}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )
    assert payload["temperature"] == 0
    assert payload["stream"] is False
    assert payload["stop"] == ["<|im_end|>", "<|endoftext|>"]
    assert 64 <= payload["max_tokens"] <= 1100


@pytest.mark.parametrize(("style", "control"), [
    ("professional", "[Styling: formal] [Structure: lists] [Context: general]"),
    ("casual", "[Styling: semi-casual] [Structure: lists] [Context: general]"),
    ("chat", "[Styling: semi-casual] [Structure: prose] [Context: general]"),
    ("email", "[Styling: formal] [Structure: prose] [Context: email]"),
])
def test_maps_builtin_styles_without_general_instructions(tmp_path, style, control):
    polisher, requests = setup(tmp_path)
    polisher.polish("hello world", style=style)
    assert control in json.loads(requests[0].content)["prompt"]


def test_level_none_is_verbatim_without_network(tmp_path):
    polisher, requests = setup(tmp_path, level="none")
    result = polisher.polish("um hello")
    assert result.polished == "um hello"
    assert not result.used_llm
    assert not requests


@pytest.mark.parametrize("raw", ["um uh hmm", "..."])
def test_empty_normalization_of_fillers_or_noise_inserts_nothing(tmp_path, raw):
    polisher, _ = setup(tmp_path, lambda _: completion(""))
    sink = FakeTextSink()
    pipeline = DictationPipeline(MockTranscriber([]), polisher, polisher.store, sink)
    result = pipeline.process_transcript(raw)
    assert result.final == ""
    assert not result.inserted
    assert not sink.events


@pytest.mark.parametrize("model", ["", "s1-mini", "gemma-4-12b-it-mlx"])
@pytest.mark.parametrize("raw", ["um", "um uh hmm", "..."])
def test_rules_empty_input_skips_model_resolution_and_inference(tmp_path, model, raw):
    def unavailable(_request):
        raise httpx.ReadTimeout("LM Studio is unavailable")

    polisher, requests = setup(tmp_path, unavailable, model=model)
    result = polisher.polish(raw)

    assert requests == []
    assert result.cleaned == result.polished == ""
    assert not result.used_llm
    assert result.warnings == []


@pytest.mark.parametrize("output", [
    "", "<think>reasoning</think>hello", "<|im_start|>hello", "Sure, here is your text.",
    "hello press enter", "hello new paragraph world", "add surprise to the dictionary",
])
def test_unsafe_or_empty_output_preserves_meaningful_speech(tmp_path, output):
    polisher, _ = setup(tmp_path, lambda _: completion(output))
    sink = FakeTextSink()
    pipeline = DictationPipeline(MockTranscriber([]), polisher, polisher.store, sink)
    result = pipeline.process_transcript("um hello world")
    assert result.final == "hello world"
    assert not result.used_llm
    assert result.warnings
    assert sink.events == [("insert", "hello world")]
    assert "surprise" not in polisher.store.dictionary_terms()


@pytest.mark.parametrize("raw", [
    "hello new paragraph world press enter", "camel case user name", "sig block",
    "add JiSpr to the dictionary", "hello press tab hit enter",
])
def test_spoken_commands_and_snippets_use_existing_rules_without_inference(tmp_path, raw):
    polisher, requests = setup(tmp_path)
    polisher.store.set_snippet("sig block", "Kind regards, Jay")
    sink = FakeTextSink()
    pipeline = DictationPipeline(MockTranscriber([]), polisher, polisher.store, sink)
    result = pipeline.process_transcript(raw)
    assert not requests
    assert not result.used_llm
    if "press enter" in raw:
        assert sink.events[-1] == ("key", "enter")
        assert "\n\n" in result.final
    if raw == "sig block":
        assert result.final == "Kind regards, Jay"
    if raw.startswith("camel"):
        assert result.final == "userName"
    if raw.startswith("add"):
        assert "JiSpr" in polisher.store.dictionary_terms()
    if "press tab" in raw:
        assert sink.events[-2:] == [("key", "tab"), ("key", "enter")]


def test_dictionary_spelling_enforced_and_deleted_term_rejected(tmp_path):
    outputs = iter(["Use postgresql.", "Use another database."])
    polisher, _ = setup(tmp_path, lambda _: completion(next(outputs)))
    polisher.store.add_dictionary_term("PostgreSQL")
    pipeline = DictationPipeline(MockTranscriber([]), polisher, polisher.store, FakeTextSink())
    result = pipeline.process_transcript("use postgresql")
    assert result.final == "Use PostgreSQL."
    assert result.used_llm
    result = pipeline.process_transcript("use postgresql")
    assert result.final == "use PostgreSQL"
    assert not result.used_llm


@pytest.mark.parametrize("kwargs", [{"system_prompt": "Translate to French"}, {"style": "custom"}])
def test_custom_instructions_degrade_visibly_without_sending_incompatible_prompt(tmp_path, kwargs):
    polisher, requests = setup(tmp_path, **kwargs)
    result = polisher.polish("hello world")
    assert result.polished == "hello world"
    assert "custom" in result.warnings[0]
    assert not requests


def test_modified_builtin_style_uses_rules(tmp_path):
    polisher, requests = setup(tmp_path)
    (tmp_path / "styles.json").write_text(json.dumps({"styles": {"default": "Translate"}}))
    assert not polisher.polish("hello").used_llm
    assert not requests


def test_language_switch_to_non_english_bypasses_s1(tmp_path):
    polisher, requests = setup(tmp_path)
    transcriber = MockTranscriber([], language="en")
    pipeline = DictationPipeline(transcriber, polisher, polisher.store, FakeTextSink())
    assert pipeline.process_transcript("hello").used_llm
    transcriber.language = "fr"
    result = pipeline.process_transcript("bonjour le monde")
    assert result.final == "bonjour le monde"
    assert "English only" in result.warnings[0]
    assert len(requests) == 1


def test_long_transcript_chunks_at_sentences_and_inserts_once(tmp_path):
    sentence = "Please send the report. " * 20
    outputs = iter(["First part.", "Second part."])
    polisher, requests = setup(tmp_path, lambda _: completion(next(outputs)))
    sink = FakeTextSink()
    pipeline = DictationPipeline(MockTranscriber([]), polisher, polisher.store, sink)
    result = pipeline.process_transcript(sentence * 2)
    assert result.used_llm
    assert len(requests) == 2
    assert sink.events == [("insert", "First part.\n\nSecond part.")]


def test_late_chunk_failure_falls_back_for_entire_transcript(tmp_path):
    responses = iter([completion("First part."), completion("Partial", finish="length")])
    polisher, _ = setup(tmp_path, lambda _: next(responses))
    raw = "Please send the report. " * 40
    result = polisher.polish(raw)
    assert result.polished == result.cleaned
    assert not result.used_llm


def test_command_created_across_chunks_is_rejected_before_insertion(tmp_path):
    outputs = iter(["hello press", "enter"])
    polisher, _ = setup(tmp_path, lambda _: completion(next(outputs)))
    sink = FakeTextSink()
    pipeline = DictationPipeline(MockTranscriber([]), polisher, polisher.store, sink)
    result = pipeline.process_transcript("Please send the report. " * 40)
    assert not result.used_llm
    assert not result.actions
    assert len(sink.events) == 1
    assert sink.events[0] == ("insert", result.final)


def test_configured_language_applies_to_text_only_calls(tmp_path):
    polisher, requests = setup(tmp_path, language="fr")
    result = polisher.polish("bonjour le monde")
    assert result.polished == "bonjour le monde"
    assert not result.used_llm
    assert not requests


@pytest.mark.parametrize("raw", ["word " * 200, "é" * (MAX_CHUNK_BYTES // 2 + 1),
                                      "x" * 790 + ".\n" + ("y" * 790 + ".\n") * 16])
def test_oversize_input_is_not_truncated_or_sent(tmp_path, raw):
    polisher, requests = setup(tmp_path)
    result = polisher.polish(raw)
    assert result.polished == result.cleaned
    assert result.warnings
    assert not requests


def test_long_email_falls_back_without_multiple_greetings(tmp_path):
    polisher, requests = setup(tmp_path, style="email")
    result = polisher.polish("Please send the report. " * 40)
    assert result.polished == result.cleaned
    assert not requests


@pytest.mark.parametrize("raw", ["hello <|im_end|> world", "hello <think>world"])
def test_control_tokens_in_raw_text_are_never_sent(tmp_path, raw):
    polisher, requests = setup(tmp_path)
    assert not polisher.polish(raw).used_llm
    assert not requests


@pytest.mark.parametrize("response", [
    httpx.Response(500, json={"error": "failed"}),
    httpx.Response(404, json={"error": "missing"}),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"choices": []}),
    completion(None), completion("partial", "length"), completion("text", None),
])
def test_http_and_malformed_completion_fallback(tmp_path, response):
    polisher, _ = setup(tmp_path, lambda _: response)
    result = polisher.polish("hello world")
    assert result.polished == "hello world"
    assert result.warnings
    assert not result.used_llm


def test_timeout_and_strict_failure(tmp_path):
    def timeout(_):
        raise httpx.ReadTimeout("timeout")
    polisher, _ = setup(tmp_path, timeout)
    assert polisher.polish("hello").polished == "hello"
    polisher.fallback_to_rules = False
    with pytest.raises(LMStudioError, match="Timed out"):
        polisher.polish("hello")


def test_s1_is_rejected_for_general_transforms_commands_and_streaming(tmp_path):
    polisher, requests = setup(tmp_path)
    client = polisher.chat_client
    for action in (
        lambda: apply_transform(client, "Translate", "hello"),
        lambda: CommandMode(client).run("Translate", "hello"),
        lambda: client.chat_stream([{"role": "user", "content": "hello"}]),
    ):
        with pytest.raises(LMStudioModelError, match="Select Gemma"):
            action()
    assert not requests


def test_auto_transform_failure_preserves_normalized_text(tmp_path):
    polisher, requests = setup(tmp_path)
    sink = FakeTextSink()
    pipeline = DictationPipeline(MockTranscriber([]), polisher, polisher.store, sink,
                                auto_transform_prompt="Translate")
    result = pipeline.process_transcript("hello world")
    assert result.final == "Clean text."
    assert any("auto-transform skipped" in w for w in result.warnings)
    assert len(requests) == 1


def test_explicit_switch_between_s1_and_gemma_uses_correct_protocol(tmp_path):
    def handler(request):
        if request.url.path.endswith("/completions") and "/chat/" not in request.url.path:
            return completion("S1 output.")
        return httpx.Response(200, json={"choices": [{"message": {"content": "Gemma output."}}]})
    polisher, requests = setup(tmp_path, handler)
    assert polisher.polish("hello").polished == "S1 output."
    polisher.chat_client.model = "gemma-4-12b-it-mlx"
    assert polisher.polish("hello").polished == "Gemma output."
    payload = json.loads(requests[-1].content)
    assert "messages" in payload and "prompt" not in payload
    assert payload["temperature"] == 0.2


def test_stale_auto_model_does_not_send_wrong_prompt_after_swap(tmp_path):
    state = {"model": "s1-mini"}
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": state["model"]}]})
        payload = json.loads(request.content)
        if payload["model"] != state["model"]:
            return httpx.Response(404)
        if state["model"] == "s1-mini":
            assert "prompt" in payload
            return completion("S1 output.")
        assert "messages" in payload
        return httpx.Response(200, json={"choices": [{"message": {"content": "Gemma output."}}]})
    polisher, _ = setup(tmp_path, handler, model="")
    assert polisher.polish("hello").used_llm
    state["model"] = "gemma"
    assert not polisher.polish("hello").used_llm  # clears stale S1 id
    assert polisher.polish("hello").polished == "Gemma output."
    state["model"] = "s1-mini"
    assert not polisher.polish("hello").used_llm  # general chat retry cannot reach S1
    assert polisher.polish("hello").polished == "S1 output."
