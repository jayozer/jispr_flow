"""Apple-Silicon Parakeet v3 adapter using the public ``parakeet-mlx`` API."""

from __future__ import annotations

import os
import shutil
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from local_flow.asr.base import Transcriber
from local_flow.errors import ASRBackendMissingError, ASRModelMissingError

DEFAULT_PARAKEET_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"


class MlxParakeetTranscriber(Transcriber):
    """Load multilingual Parakeet directly in JiSpr, without LM Studio.

    ``parakeet-mlx`` intentionally exposes a path-based transcription API.
    Live microphone PCM is therefore written to a short-lived WAV and removed
    even when decoding fails. The model handles language detection itself;
    ``language`` is retained only for the common runtime adapter shape.

    Parakeet currently has no public initial-prompt/vocabulary-bias argument.
    The inherited ``set_vocabulary_provider`` no-op is deliberate: JiSpr still
    supplies dictionary terms to polish and enforces them after polishing.
    """

    def __init__(
        self,
        model: str = DEFAULT_PARAKEET_MODEL,
        language: str | None = "auto",
    ) -> None:
        if shutil.which("ffmpeg") is None:
            raise ASRBackendMissingError(
                "The mlx-parakeet backend requires FFmpeg.",
                hint="Install FFmpeg (for example `brew install ffmpeg`) and retry.",
            )
        try:
            from parakeet_mlx import from_pretrained
        except ImportError as exc:
            raise ASRBackendMissingError(
                "The 'parakeet-mlx' package is not installed.",
                hint="Install the optional backend with `uv sync --extra parakeet-asr`.",
            ) from exc

        self.model_name = model or DEFAULT_PARAKEET_MODEL
        self._language = language
        # MLX streams belong to the thread that creates them. JiSpr's live
        # pipeline builds adapters on its main thread but runs ASR on a
        # persistent processing worker. Keep model construction and every
        # inference on this one owned thread so file, live, recovery, and
        # preview callers can safely invoke the adapter from any thread.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="jispr-parakeet"
        )
        try:
            self._model = self._executor.submit(
                from_pretrained, self.model_name
            ).result()
        except Exception as exc:
            self._executor.shutdown(wait=True, cancel_futures=True)
            raise ASRModelMissingError(
                f"Parakeet could not load model {self.model_name!r}: {exc}",
                hint="Use mlx-community/parakeet-tdt-0.6b-v3 or a compatible local "
                "Parakeet MLX model; first use needs network access.",
            ) from exc

    @property
    def language(self) -> str | None:
        return self._language

    @language.setter
    def language(self, value: str | None) -> None:
        # Parakeet v3 detects its supported languages. Keep the configured
        # value so tray/controller code can retain one adapter interface.
        self._language = value

    def prepare(self) -> None:
        # ``from_pretrained`` eagerly loads the weights in __init__, so the
        # benchmark's constructor timer already captures model load time.
        return None

    def _transcribe_path(self, path: Path) -> str:
        try:
            result = self._executor.submit(
                self._model.transcribe, str(path)
            ).result()
        except Exception as exc:
            raise ASRModelMissingError(
                f"Parakeet could not transcribe with model {self.model_name!r}: {exc}",
                hint="Check that the audio is readable, FFmpeg is installed, and the "
                "Parakeet model cache is complete.",
            ) from exc
        return str(getattr(result, "text", "")).strip()

    def transcribe_path(self, path: Path) -> str:
        return self._transcribe_path(Path(path))

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        even_pcm = pcm[: len(pcm) // 2 * 2]
        temp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_path = Path(temp.name)
        temp.close()
        try:
            with wave.open(str(temp_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(even_pcm)
            return self._transcribe_path(temp_path)
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
