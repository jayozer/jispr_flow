"""Speech-to-text interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence


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

    def set_vocabulary_provider(
        self, provider: Callable[[], Sequence[str]]
    ) -> None:
        """Supply prioritized vocabulary dynamically before each ASR call.

        Backends without a vocabulary-bias feature intentionally ignore the
        provider. Keeping this hook non-abstract preserves third-party/test
        adapters while letting capable engines observe dictionary additions
        without being rebuilt or coupled to the personalization store.
        """
        return None

    def prepare(self) -> None:
        """Eagerly initialize a lazy backend for accurate load benchmarks."""
        return None
