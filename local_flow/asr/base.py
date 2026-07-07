"""Speech-to-text interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Transcriber(ABC):
    """Speech-to-text backend.

    Implementations may accept a ``language`` constructor argument (ISO
    639-1 code, or ``"auto"``/``None`` to auto-detect per utterance); it is
    not part of this abstract interface because backends differ in how
    they express "detect the language".
    """

    @abstractmethod
    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        """Transcribe 16-bit mono PCM to text (may be empty for silence)."""
