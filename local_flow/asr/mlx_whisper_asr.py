"""Optional Apple-Silicon MLX Whisper transcriber adapter."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from local_flow.asr.base import Transcriber
from local_flow.asr.vocabulary import build_initial_prompt
from local_flow.errors import ASRBackendMissingError, ASRModelMissingError


def _resolve_language(raw: str | None) -> str | None:
    return None if raw is not None and raw.lower() == "auto" else raw


class MlxWhisperTranscriber(Transcriber):
    """MLX Whisper through its documented public ``transcribe`` function.

    The model is lazy-loaded by mlx-whisper on the first transcription.
    :meth:`prepare` performs a tiny silent call so the benchmark harness can
    report that initialization separately from measured utterance latency.
    """

    def __init__(
        self,
        model: str = "mlx-community/whisper-small.en-mlx",
        language: str | None = "en",
        vocabulary_provider: Callable[[], Sequence[str]] | None = None,
    ) -> None:
        try:
            import mlx_whisper
            import numpy as np
        except ImportError as exc:
            raise ASRBackendMissingError(
                "The 'mlx-whisper' package is not installed.",
                hint="Install the optional MLX ASR dependency on Apple Silicon.",
            ) from exc
        self._mlx_whisper = mlx_whisper
        self._np = np
        self.model_name = model
        self._language = language
        self._vocabulary_provider = vocabulary_provider or (lambda: ())
        self._prepared = False

    @property
    def language(self) -> str | None:
        return self._language

    @language.setter
    def language(self, value: str | None) -> None:
        self._language = value

    def set_vocabulary_provider(self, provider: Callable[[], Sequence[str]]) -> None:
        self._vocabulary_provider = provider

    def _options(self, *, include_vocabulary: bool = True) -> dict[str, object]:
        options: dict[str, object] = {
            "path_or_hf_repo": self.model_name,
            "verbose": None,
            "language": _resolve_language(self._language),
        }
        if include_vocabulary:
            prompt = build_initial_prompt(self._vocabulary_provider())
            if prompt:
                options["initial_prompt"] = prompt
        return options

    def _transcribe(self, audio, *, include_vocabulary: bool = True) -> str:
        try:
            result = self._mlx_whisper.transcribe(
                audio, **self._options(include_vocabulary=include_vocabulary)
            )
        except Exception as exc:
            raise ASRModelMissingError(
                f"MLX Whisper could not transcribe with model {self.model_name!r}: {exc}",
                hint="Use an MLX Whisper model directory or Hugging Face repo such as "
                "mlx-community/whisper-small.en-mlx; first use needs network access.",
            ) from exc
        if not isinstance(result, dict):
            return ""
        return str(result.get("text", "")).strip()

    def prepare(self) -> None:
        if self._prepared:
            return
        # mlx-whisper loads and caches the model inside its public transcribe
        # API. A tenth-second silent waveform keeps extra inference negligible
        # while ensuring model download/load is captured before measured runs.
        silence = self._np.zeros(1600, dtype=self._np.float32)
        self._transcribe(silence, include_vocabulary=False)
        self._prepared = True

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        audio = self._np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=self._np.int16)
        audio = audio.astype(self._np.float32) / 32768.0
        if sample_rate != 16000:
            target_len = int(len(audio) * 16000 / sample_rate)
            if target_len > 0:
                positions = self._np.linspace(0, len(audio) - 1, target_len)
                audio = self._np.interp(
                    positions, self._np.arange(len(audio)), audio
                ).astype(self._np.float32)
        return self._transcribe(audio)

    def transcribe_path(self, path: Path) -> str:
        return self._transcribe(str(path))
