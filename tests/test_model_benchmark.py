"""Headless tests for the frozen-ASR model benchmark."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

import local_flow.app as app_module
import local_flow.benchmark_models as benchmark_module
from local_flow.app import main
from local_flow.asr.mock import MockTranscriber
from local_flow.benchmark_models import (
    CorpusCase,
    FrozenTranscript,
    apply_reviews,
    benchmark_polishers,
    filler_removal_rate,
    freeze_asr,
    load_corpus,
    punctuation_f1,
)
from local_flow.errors import LocalFlowError
from local_flow.llm.lmstudio import StreamResult
from local_flow.personalization.store import PersonalizationStore


def _write_wav(path: Path, duration_s: float = 1.0) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * int(duration_s * 16000))


def _case(audio: Path) -> CorpusCase:
    return CorpusCase(
        id="names",
        audio=str(audio),
        language="en",
        verbatim="um JiSpr Flow ships at 3.14",
        intended="JiSpr Flow ships at 3.14.",
        category="protected",
        proper_names=["JiSpr Flow"],
        protected_tokens=["3.14"],
        fillers=["um"],
    )


class FakeStreamingClient:
    def __init__(self, model: str, calls: list[tuple[str, list[dict]]]):
        self.model = model
        self.calls = calls
        self.closed = False

    def chat_stream(self, messages):
        self.calls.append((self.model, messages))
        latency = 0.1 if self.model == "fast" else 0.5
        return StreamResult(
            text="JiSpr Flow ships at 3.14.", first_token_s=latency / 2, total_s=latency
        )

    def close(self):
        self.closed = True


def test_manifest_resolves_audio_and_validates_ids(tmp_path):
    wav = tmp_path / "sample.wav"
    _write_wav(wav)
    manifest = tmp_path / "corpus.jsonl"
    row = {
        "id": "one",
        "audio": "sample.wav",
        "language": "en",
        "verbatim": "hello",
        "intended": "Hello.",
        "category": "general",
    }
    manifest.write_text(json.dumps(row) + "\n")

    cases = load_corpus(manifest)

    assert cases[0].audio == str(wav)
    manifest.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
    with pytest.raises(LocalFlowError, match="unique"):
        load_corpus(manifest)


def test_freeze_asr_transcribes_each_case_once(tmp_path):
    wav = tmp_path / "sample.wav"
    _write_wav(wav, 2.0)
    transcriber = MockTranscriber(["um JiSpr Flow ships at 3.14"])
    times = iter([0.0, 0.2, 1.0, 1.3])

    load_s, rows = freeze_asr([_case(wav)], lambda: transcriber, clock=lambda: next(times))

    assert load_s == 0.2
    assert rows[0].asr_latency_s == pytest.approx(0.3)
    assert rows[0].audio_duration_s == 2.0
    assert rows[0].raw == "um JiSpr Flow ships at 3.14"
    assert len(transcriber.calls) == 1


def test_polishers_receive_byte_identical_frozen_input_and_report_metrics(tmp_path):
    wav = tmp_path / "sample.wav"
    _write_wav(wav)
    case = _case(wav)
    frozen = [
        FrozenTranscript(
            case_id=case.id,
            audio=case.audio,
            language="en",
            raw="um JiSpr Flow ships at 3.14",
            asr_latency_s=0.2,
            audio_duration_s=1.0,
            wer=0.0,
        )
    ]
    calls: list[tuple[str, list[dict]]] = []
    store = PersonalizationStore(tmp_path / "data")

    report = benchmark_polishers(
        [case],
        frozen,
        ["fast", "slow"],
        lambda model: FakeStreamingClient(model, calls),
        store,
        cleanup_level="medium",
        style="default",
        system_prompt="",
        runs=1,
    )

    assert [row["raw"] for row in report["results"]] == [frozen[0].raw] * 2
    assert calls[0][1] == calls[1][1]
    assert all(row["proper_name_accuracy"] == 1.0 for row in report["results"])
    assert all(row["protected_token_accuracy"] == 1.0 for row in report["results"])
    assert report["review_status"] == "pending"
    assert report["recommendation"] is None


def test_completed_reviews_apply_safety_gate_and_choose_fastest_eligible(tmp_path):
    wav = tmp_path / "sample.wav"
    _write_wav(wav)
    case = _case(wav)
    frozen = [
        FrozenTranscript(case.id, case.audio, "en", case.verbatim, 0.2, 1.0, 0.0)
    ]
    report = benchmark_polishers(
        [case],
        frozen,
        ["fast", "slow"],
        lambda model: FakeStreamingClient(model, []),
        PersonalizationStore(tmp_path / "data"),
        cleanup_level="medium",
        style="default",
        system_prompt="",
    )
    reviews = []
    for item in report["blind_reviews"]:
        reviews.append(
            {
                "blind_id": item["blind_id"],
                "material_meaning_change": False,
                "hallucination": item["blind_id"]
                == next(
                    row["blind_id"] for row in report["results"] if row["model"] == "fast"
                ),
            }
        )

    reviewed = apply_reviews(report, reviews)

    assert reviewed["unsafe_models"] == ["fast"]
    assert reviewed["recommendation"] == "slow"


def test_blind_reviews_are_bound_to_exact_generated_output(tmp_path):
    class ChangedOutputClient(FakeStreamingClient):
        def chat_stream(self, messages):
            result = super().chat_stream(messages)
            return StreamResult(
                text="JiSpr Flow ships tomorrow.",
                first_token_s=result.first_token_s,
                total_s=result.total_s,
            )

    wav = tmp_path / "sample.wav"
    _write_wav(wav)
    case = _case(wav)
    frozen = [
        FrozenTranscript(case.id, case.audio, "en", case.verbatim, 0.2, 1.0, 0.0)
    ]
    kwargs = {
        "store": PersonalizationStore(tmp_path / "data"),
        "cleanup_level": "medium",
        "style": "default",
        "system_prompt": "",
    }
    reviewed_report = benchmark_polishers(
        [case], frozen, ["fast"], lambda model: FakeStreamingClient(model, []), **kwargs
    )
    changed_report = benchmark_polishers(
        [case], frozen, ["fast"], lambda model: ChangedOutputClient(model, []), **kwargs
    )
    reviews = [
        {**item, "material_meaning_change": False, "hallucination": False}
        for item in reviewed_report["blind_reviews"]
    ]

    with pytest.raises(LocalFlowError, match="ids must exactly match"):
        apply_reviews(changed_report, reviews)


def test_cli_applies_reviews_to_saved_outputs_without_asr_or_lmstudio(
    tmp_path, monkeypatch, capsys
):
    wav = tmp_path / "sample.wav"
    _write_wav(wav)
    case = _case(wav)
    manifest = tmp_path / "corpus.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "id": case.id,
                "audio": case.audio,
                "language": case.language,
                "verbatim": case.verbatim,
                "intended": case.intended,
                "category": case.category,
                "proper_names": case.proper_names,
                "protected_tokens": case.protected_tokens,
                "fillers": case.fillers,
            }
        )
        + "\n"
    )
    frozen = [
        FrozenTranscript(case.id, case.audio, "en", case.verbatim, 0.2, 1.0, 0.0)
    ]
    report = benchmark_polishers(
        [case],
        frozen,
        ["fast"],
        lambda model: FakeStreamingClient(model, []),
        PersonalizationStore(tmp_path / "data"),
        cleanup_level="medium",
        style="default",
        system_prompt="",
    )
    source = tmp_path / "source"
    source.mkdir()
    report_path = source / "model-benchmark.json"
    report_path.write_text(json.dumps(report))
    reviews_path = source / "blind-review.jsonl"
    reviews_path.write_text(
        "".join(
            json.dumps(
                {
                    **item,
                    "material_meaning_change": False,
                    "hallucination": False,
                }
            )
            + "\n"
            for item in report["blind_reviews"]
        )
    )

    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("review application must not rerun ASR or LM Studio")

    monkeypatch.setattr(app_module, "_build_transcriber", unexpected_call)
    monkeypatch.setattr(benchmark_module, "benchmark_polishers", unexpected_call)
    output = tmp_path / "reviewed"

    code = main(
        [
            "benchmark-models",
            str(manifest),
            "--output",
            str(output),
            "--benchmark-report",
            str(report_path),
            "--reviews",
            str(reviews_path),
        ]
    )

    assert code == 0
    reviewed = json.loads((output / "model-benchmark.json").read_text())
    assert reviewed["recommendation"] == "fast"
    assert "Reviewed saved benchmark" in capsys.readouterr().out


def test_metric_helpers_cover_empty_and_partial_cases():
    assert punctuation_f1("Hello", "Hello") == 1.0
    assert punctuation_f1("Hello, world!", "Hello world.") == 0.0
    assert filler_removal_rate(["um", "uh"], "Um, hello") == 0.5


def test_cli_freeze_only_writes_private_frozen_rows(tmp_path, monkeypatch, capsys):
    wav = tmp_path / "sample.wav"
    _write_wav(wav)
    manifest = tmp_path / "corpus.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "id": "one",
                "audio": str(wav),
                "language": "en",
                "verbatim": "mock transcription",
                "intended": "Mock transcription.",
                "category": "general",
            }
        )
        + "\n"
    )
    output = tmp_path / "private-results"
    monkeypatch.setattr(
        app_module, "_build_transcriber", lambda _config: MockTranscriber(["mock transcription"])
    )

    code = main(
        [
            "benchmark-models",
            str(manifest),
            "--output",
            str(output),
            "--freeze-only",
        ]
    )

    assert code == 0
    frozen = [json.loads(line) for line in (output / "frozen-asr.jsonl").read_text().splitlines()]
    assert frozen[0]["raw"] == "mock transcription"
    assert "Frozen ASR" in capsys.readouterr().out
