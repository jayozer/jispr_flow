"""Speech-to-text interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Transcriber(ABC):
    @abstractmethod
    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        """Transcribe 16-bit mono PCM to text (may be empty for silence)."""
