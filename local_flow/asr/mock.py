"""Scripted transcriber for tests and the headless demo."""

from __future__ import annotations

from collections.abc import Sequence

from local_flow.asr.base import Transcriber


class MockTranscriber(Transcriber):
    """Returns pre-scripted texts, one per call (last one repeats)."""

    def __init__(self, scripted: Sequence[str], language: str | None = None) -> None:
        self._texts = list(scripted)
        self._language = language  # accepted for interface parity; unused in transcribe()
        self.calls: list[tuple[int, int]] = []  # (pcm byte length, sample rate)

    @property
    def language(self) -> str | None:
        """Raw configured language; same settable-property shape as
        :class:`~local_flow.asr.faster_whisper_asr.FasterWhisperTranscriber` so
        callers (e.g. the tray's Language submenu) can treat both backends
        identically, even though this mock never consults it.
        """
        return self._language

    @language.setter
    def language(self, value: str | None) -> None:
        self._language = value

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        index = min(len(self.calls), len(self._texts) - 1) if self._texts else 0
        self.calls.append((len(pcm), sample_rate))
        return self._texts[index] if self._texts else ""
