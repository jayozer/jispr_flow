"""Headless contract tests for the experimental MLX Whisper adapter."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from local_flow.asr.mlx_whisper_asr import MlxWhisperTranscriber


class FakeArray:
    def __init__(self, length, dtype):
        self._length = length
        self.dtype = dtype

    def __len__(self):
        return self._length

    def astype(self, dtype):
        return FakeArray(self._length, dtype)

    def __truediv__(self, _divisor):
        return FakeArray(self._length, self.dtype)


class FakeNumpy:
    """The tiny NumPy surface the adapter needs; keeps core tests extra-free."""

    int16 = "int16"
    float32 = "float32"

    @staticmethod
    def frombuffer(data, dtype):
        return FakeArray(len(data) // 2, dtype)

    @staticmethod
    def zeros(length, dtype):
        return FakeArray(length, dtype)


class FakeMlxWhisper:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **options):
        self.calls.append((audio, options))
        return {"text": " MLX transcript "}


def _transcriber(terms=None):
    transcriber = object.__new__(MlxWhisperTranscriber)
    transcriber._mlx_whisper = FakeMlxWhisper()
    transcriber._np = FakeNumpy()
    transcriber.model_name = "mlx-community/whisper-small.en-mlx"
    transcriber._language = "en"
    transcriber._vocabulary_provider = lambda: terms or []
    transcriber._prepared = False
    return transcriber


class TestMlxWhisperTranscriber:
    def test_live_pcm_is_normalized_and_vocabulary_is_forwarded(self):
        terms = ["JiSpr Flow"]
        transcriber = _transcriber(terms)

        text = transcriber.transcribe(b"\xff\x7f\x00\x80", 16000)

        audio, options = transcriber._mlx_whisper.calls[0]
        assert text == "MLX transcript"
        assert audio.dtype == FakeNumpy.float32
        assert options["path_or_hf_repo"] == transcriber.model_name
        assert options["initial_prompt"] == "Important vocabulary: JiSpr Flow"
        assert options["language"] == "en"

    def test_file_path_and_dynamic_terms_use_public_api(self):
        terms = ["first"]
        transcriber = _transcriber(terms)
        transcriber.transcribe_path(Path("memo.wav"))
        terms.append("second")
        transcriber.transcribe_path(Path("memo.wav"))

        first = transcriber._mlx_whisper.calls[0]
        second = transcriber._mlx_whisper.calls[1]
        assert first[0] == "memo.wav"
        assert first[1]["initial_prompt"] == "Important vocabulary: first"
        assert second[1]["initial_prompt"] == "Important vocabulary: first, second"

    def test_prepare_initializes_once_without_vocabulary(self):
        transcriber = _transcriber(["do not include during warmup"])

        transcriber.prepare()
        transcriber.prepare()

        assert len(transcriber._mlx_whisper.calls) == 1
        audio, options = transcriber._mlx_whisper.calls[0]
        assert len(audio) == 1600
        assert "initial_prompt" not in options

    def test_auto_language_maps_to_none(self):
        transcriber = _transcriber()
        transcriber._language = "auto"
        transcriber.transcribe_path(Path("memo.wav"))
        assert transcriber._mlx_whisper.calls[0][1]["language"] is None


def test_constructor_uses_installed_modules(monkeypatch):
    fake_module = FakeMlxWhisper()
    monkeypatch.setitem(
        __import__("sys").modules,
        "mlx_whisper",
        SimpleNamespace(transcribe=fake_module.transcribe),
    )
    monkeypatch.setitem(__import__("sys").modules, "numpy", FakeNumpy())

    transcriber = MlxWhisperTranscriber(model="local-model", language="en")

    assert transcriber.model_name == "local-model"
