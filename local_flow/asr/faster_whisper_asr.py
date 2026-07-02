"""faster-whisper ASR backend (optional extra: ``uv sync --extra asr``)."""

from __future__ import annotations

from local_flow.asr.base import Transcriber
from local_flow.errors import ASRBackendMissingError, ASRModelMissingError


class FasterWhisperTranscriber(Transcriber):
    def __init__(
        self,
        model: str = "small.en",
        device: str = "auto",
        compute_type: str = "int8",
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
        segments, _info = self._model.transcribe(audio, beam_size=1)
        return " ".join(segment.text.strip() for segment in segments).strip()
