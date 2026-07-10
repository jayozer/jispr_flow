"""Headless tests for the repeatable ASR benchmark harness and CLI."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

import local_flow.app as app_module
from local_flow.app import main
from local_flow.asr.benchmark import (
    audio_duration_s,
    benchmark_files,
    render_report,
    word_error_rate,
)
from local_flow.asr.mock import MockTranscriber
from local_flow.errors import LocalFlowError


def _write_wav(path: Path, duration_s: float = 2.0, sample_rate: int = 16000) -> None:
    frames = int(duration_s * sample_rate)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * frames)


class TestWordErrorRate:
    def test_case_and_punctuation_are_normalized(self):
        assert word_error_rate("Hello, JiSpr Flow!", "hello jispr flow") == 0.0

    def test_insert_delete_and_substitute(self):
        assert word_error_rate("one two three", "one four extra") == pytest.approx(2 / 3)

    def test_empty_reference(self):
        assert word_error_rate("", "") == 0.0
        assert word_error_rate("", "unexpected") == 1.0


class TestBenchmarkFiles:
    def test_reports_load_latency_percentile_rtf_transcript_and_wer(self, tmp_path):
        wav = tmp_path / "sample.wav"
        _write_wav(wav, duration_s=2.0)
        times = iter([0.0, 0.5, 1.0, 2.0, 3.0, 5.0])
        transcriber = MockTranscriber(["hello world"])

        report = benchmark_files(
            [wav],
            lambda: transcriber,
            backend="mock",
            model="fixture",
            device="cpu",
            compute_type="int8",
            references=["hello world"],
            runs=2,
            warmup_runs=0,
            clock=lambda: next(times),
        )

        result = report.files[0]
        assert report.model_load_time_s == 0.5
        assert result.duration_s == 2.0
        assert [run.latency_s for run in result.runs] == [1.0, 2.0]
        assert result.median_latency_s == 1.5
        assert result.p95_latency_s == 2.0
        assert result.median_real_time_factor == 0.75
        assert result.transcript == "hello world"
        assert result.wer == 0.0
        assert report.aggregate_median_latency_s == 1.5
        assert report.aggregate_wer == 0.0
        assert "median 1.500s" in render_report(report)

    def test_warmup_is_unmeasured_and_reuses_loaded_transcriber(self, tmp_path):
        wav = tmp_path / "sample.wav"
        _write_wav(wav)
        times = iter([0.0, 0.25, 1.0, 1.5])
        transcriber = MockTranscriber(["warmup", "measured"])

        report = benchmark_files(
            [wav],
            lambda: transcriber,
            backend="mock",
            model="fixture",
            device="cpu",
            compute_type="int8",
            runs=1,
            warmup_runs=1,
            clock=lambda: next(times),
        )

        assert len(transcriber.calls) == 2
        assert report.files[0].transcript == "measured"
        assert report.files[0].runs[0].latency_s == 0.5

    def test_validates_run_and_reference_counts(self, tmp_path):
        wav = tmp_path / "sample.wav"
        _write_wav(wav)
        kwargs = {
            "backend": "mock",
            "model": "fixture",
            "device": "cpu",
            "compute_type": "int8",
        }

        with pytest.raises(LocalFlowError, match="runs"):
            benchmark_files([wav], lambda: MockTranscriber(["x"]), runs=0, **kwargs)
        with pytest.raises(LocalFlowError, match="references"):
            benchmark_files(
                [wav], lambda: MockTranscriber(["x"]), references=[], **kwargs
            )


class TestBenchmarkCli:
    def test_human_and_json_outputs_are_repeatable(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")
        wav = tmp_path / "sample.wav"
        output = tmp_path / "report.json"
        _write_wav(wav, duration_s=1.0)

        code = main(
            [
                "benchmark-asr",
                str(wav),
                "--reference",
                "mock transcription",
                "--runs",
                "2",
                "--warmup",
                "0",
                "--json",
                str(output),
            ]
        )

        assert code == 0
        assert "ASR benchmark" in capsys.readouterr().out
        payload = json.loads(output.read_text())
        assert payload["schema_version"] == 1
        assert payload["backend"] == "mock"
        assert payload["measured_runs"] == 2
        assert payload["files"][0]["wer"] == 0.0
        assert len(payload["files"][0]["runs"]) == 2

    def test_reference_count_must_match_files(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("LOCAL_FLOW_ASR_BACKEND", "mock")
        first = tmp_path / "first.wav"
        second = tmp_path / "second.wav"
        _write_wav(first)
        _write_wav(second)

        code = main(
            [
                "benchmark-asr",
                str(first),
                str(second),
                "--reference",
                "only one",
            ]
        )

        assert code == 1
        assert "references must match" in capsys.readouterr().err

    def test_accuracy_profile_selects_mlx_turbo(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("LOCAL_FLOW_ASR_PROFILE", "custom")
        wav = tmp_path / "sample.wav"
        _write_wav(wav, duration_s=1.0)
        captured = []

        def build(config):
            captured.append(config)
            return MockTranscriber(["profile transcript"])

        monkeypatch.setattr(app_module, "_build_transcriber", build)

        code = main(
            [
                "benchmark-asr",
                str(wav),
                "--profile",
                "accuracy",
                "--runs",
                "1",
                "--warmup",
                "0",
            ]
        )

        assert code == 0
        assert captured[0].asr_backend == "mlx-whisper"
        assert captured[0].asr_model == "mlx-community/whisper-large-v3-turbo"
        assert "whisper-large-v3-turbo" in capsys.readouterr().out

    def test_profile_rejects_concrete_model_override(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("LOCAL_FLOW_ASR_PROFILE", "custom")
        wav = tmp_path / "sample.wav"
        _write_wav(wav)

        code = main(
            [
                "benchmark-asr",
                str(wav),
                "--profile",
                "fast",
                "--model",
                "another-model",
            ]
        )

        assert code == 1
        assert "cannot be combined" in capsys.readouterr().err


def test_audio_duration_reads_wav_without_optional_dependencies(tmp_path):
    wav = tmp_path / "duration.wav"
    _write_wav(wav, duration_s=1.25)
    assert audio_duration_s(wav) == 1.25
