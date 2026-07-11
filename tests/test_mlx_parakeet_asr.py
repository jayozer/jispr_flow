"""Headless contract tests for the MLX Parakeet v3 adapter."""

from __future__ import annotations

import sys
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_flow.asr.mlx_parakeet_asr import (
    DEFAULT_PARAKEET_MODEL,
    MlxParakeetTranscriber,
)
from local_flow.errors import ASRBackendMissingError, ASRModelMissingError


class FakeParakeetModel:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, bool, int, int, bytes]] = []
        self.call_threads: list[int] = []

    def transcribe(self, path: str):
        self.call_threads.append(threading.get_ident())
        if self.fail:
            self.failed_path = path
            raise RuntimeError("decode failed")
        with wave.open(path, "rb") as wav_file:
            self.calls.append(
                (
                    path,
                    Path(path).exists(),
                    wav_file.getframerate(),
                    wav_file.getnchannels(),
                    wav_file.readframes(wav_file.getnframes()),
                )
            )
        return SimpleNamespace(text=" Parakeet transcript ")


def _install_fake(monkeypatch, model: FakeParakeetModel):
    loaded: list[str] = []

    def from_pretrained(name: str):
        loaded.append(name)
        model.load_thread = threading.get_ident()
        return model

    monkeypatch.setattr("local_flow.asr.mlx_parakeet_asr.shutil.which", lambda _name: "/ffmpeg")
    monkeypatch.setitem(
        sys.modules,
        "parakeet_mlx",
        SimpleNamespace(from_pretrained=from_pretrained),
    )
    return loaded


def test_constructor_loads_v3_directly(monkeypatch):
    loaded = _install_fake(monkeypatch, FakeParakeetModel())

    transcriber = MlxParakeetTranscriber()

    assert loaded == [DEFAULT_PARAKEET_MODEL]
    assert transcriber.language == "auto"


def test_live_pcm_uses_temporary_wav_and_removes_it(monkeypatch):
    model = FakeParakeetModel()
    _install_fake(monkeypatch, model)
    transcriber = MlxParakeetTranscriber(language="en")

    text = transcriber.transcribe(b"\x01\x00\x02\x00\xff", 22050)

    path, existed_during_call, rate, channels, frames = model.calls[0]
    assert text == "Parakeet transcript"
    assert existed_during_call is True
    assert rate == 22050
    assert channels == 1
    assert frames == b"\x01\x00\x02\x00"
    assert not Path(path).exists()


def test_model_load_and_inference_share_owned_worker_thread(monkeypatch):
    model = FakeParakeetModel()
    _install_fake(monkeypatch, model)
    caller_thread = threading.get_ident()
    transcriber = MlxParakeetTranscriber()

    transcriber.transcribe(b"\x00\x00", 16000)

    assert model.load_thread == model.call_threads[0]
    assert model.load_thread != caller_thread


def test_live_pcm_removes_temporary_wav_after_failure(monkeypatch):
    model = FakeParakeetModel(fail=True)
    _install_fake(monkeypatch, model)
    transcriber = MlxParakeetTranscriber()

    with pytest.raises(ASRModelMissingError, match="could not transcribe"):
        transcriber.transcribe(b"\x00\x00", 16000)

    assert not Path(model.failed_path).exists()


def test_transcribe_path_uses_public_path_api(monkeypatch, tmp_path):
    model = FakeParakeetModel()
    _install_fake(monkeypatch, model)
    transcriber = MlxParakeetTranscriber()
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00")

    assert transcriber.transcribe_path(path) == "Parakeet transcript"
    assert model.calls[0][0] == str(path)


def test_missing_ffmpeg_fails_before_optional_import(monkeypatch):
    monkeypatch.setattr("local_flow.asr.mlx_parakeet_asr.shutil.which", lambda _name: None)

    with pytest.raises(ASRBackendMissingError, match="FFmpeg") as exc_info:
        MlxParakeetTranscriber()

    assert "brew install ffmpeg" in exc_info.value.hint


def test_model_load_failure_is_actionable(monkeypatch):
    monkeypatch.setattr("local_flow.asr.mlx_parakeet_asr.shutil.which", lambda _name: "/ffmpeg")

    def broken(_name: str):
        raise RuntimeError("missing weights")

    monkeypatch.setitem(sys.modules, "parakeet_mlx", SimpleNamespace(from_pretrained=broken))

    with pytest.raises(ASRModelMissingError, match="missing weights") as exc_info:
        MlxParakeetTranscriber()

    assert "parakeet-tdt-0.6b-v3" in exc_info.value.hint
