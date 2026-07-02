"""Scripted transcriber for tests and the headless demo."""

from __future__ import annotations

from collections.abc import Sequence

from local_flow.asr.base import Transcriber


class MockTranscriber(Transcriber):
    """Returns pre-scripted texts, one per call (last one repeats)."""

    def __init__(self, scripted: Sequence[str]) -> None:
        self._texts = list(scripted)
        self.calls: list[tuple[int, int]] = []  # (pcm byte length, sample rate)

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        index = min(len(self.calls), len(self._texts) - 1) if self._texts else 0
        self.calls.append((len(pcm), sample_rate))
        return self._texts[index] if self._texts else ""
