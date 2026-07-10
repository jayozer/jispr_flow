"""faster-whisper ASR backend (optional extra: ``uv sync --extra asr``)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from local_flow.asr.base import Transcriber
from local_flow.asr.vocabulary import build_initial_prompt
from local_flow.errors import ASRBackendMissingError, ASRModelMissingError


def resolve_language(raw: str | None) -> str | None:
    """Map a raw configured ``language`` value to faster-whisper's argument.

    ``"auto"`` (case-insensitive) means "detect the spoken language per
    utterance", which faster-whisper expresses as ``language=None``; any
    other value (including ``None``) passes through unchanged. Kept as a
    standalone pure function so the mapping is testable without constructing
    a (model-loading) :class:`FasterWhisperTranscriber`.
    """
    if raw is not None and raw.lower() == "auto":
        return None
    return raw


class FasterWhisperTranscriber(Transcriber):
    def __init__(
        self,
        model: str = "small.en",
        device: str = "auto",
        compute_type: str = "int8",
        language: str | None = "en",
        vocabulary_provider: Callable[[], Sequence[str]] | None = None,
    ) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ASRBackendMissingError(
                "The 'faster-whisper' package is not installed.",
                hint="Install ASR extras: uv sync --extra asr "
                "(or set LOCAL_FLOW_ASR_BACKEND=mock for testing).",
            ) from exc
        self.model_name = model
        # Stored as configured (raw); "auto" -> None is applied per-call by
        # `resolve_language`, not baked in here, so the `language` property
        # setter can swap it live (e.g. from the tray's Language submenu).
        self._language = language
        self._vocabulary_provider = vocabulary_provider or (lambda: ())
        try:
            self._model = WhisperModel(model, device=device, compute_type=compute_type)
        except Exception as exc:
            raise ASRModelMissingError(
                f"Could not load the ASR model {model!r}: {exc}",
                hint="Use a known model name (tiny.en, base.en, small.en, ...) to "
                "download it once into the local cache, or set "
                "LOCAL_FLOW_ASR_MODEL to a directory containing a CTranslate2 "
                "Whisper model. Downloads require network access the first time.",
            ) from exc

    @property
    def language(self) -> str | None:
        """Raw configured language (pre :func:`resolve_language` mapping)."""
        return self._language

    @language.setter
    def language(self, value: str | None) -> None:
        self._language = value

    def set_vocabulary_provider(self, provider: Callable[[], Sequence[str]]) -> None:
        self._vocabulary_provider = provider

    def _transcribe_options(self) -> dict[str, object]:
        options: dict[str, object] = {
            "beam_size": 1,
            "language": resolve_language(self._language),
        }
        initial_prompt = build_initial_prompt(self._vocabulary_provider())
        if initial_prompt:
            options["initial_prompt"] = initial_prompt
        return options

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        import numpy as np  # ships with faster-whisper

        audio = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16)
        audio = audio.astype(np.float32) / 32768.0
        if sample_rate != 16000:
            # Whisper expects 16 kHz; do a cheap linear resample.
            target_len = int(len(audio) * 16000 / sample_rate)
            if target_len > 0:
                positions = np.linspace(0, len(audio) - 1, target_len)
                audio = np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)
        segments, _info = self._model.transcribe(audio, **self._transcribe_options())
        return " ".join(segment.text.strip() for segment in segments).strip()

    def transcribe_path(self, path: Path) -> str:
        """Transcribe an audio file directly (``local-flow transcribe``).

        Passes the path straight to faster-whisper: its bundled PyAV decodes
        the container (WAV/MP3/M4A/FLAC/...) and resamples to whatever the
        model expects internally, so -- unlike :meth:`transcribe` above,
        which only ever sees raw 16-bit PCM off the microphone -- there is no
        hand-rolled resampling here to duplicate or drift out of sync with.
        """
        segments, _info = self._model.transcribe(str(path), **self._transcribe_options())
        return " ".join(segment.text.strip() for segment in segments).strip()
