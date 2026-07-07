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


class MockStream:
    """Scripted :class:`~local_flow.asr.streaming.TranscriberStream` for tests.

    Returns the queued ``partials`` one at a time, one per ``frames_per_partial``
    fed frames (default: every frame) -- a stand-in for "one per `interval_ms`
    worth of new audio" that doesn't need real audio content or timing to
    drive from a test. ``finish()`` returns the last scripted partial (the
    "full-buffer" final text); ``reset()`` drops all state.
    """

    def __init__(self, partials: Sequence[str], frames_per_partial: int = 1) -> None:
        self._partials = list(partials)
        self._frames_per_partial = max(1, frames_per_partial)
        self.fed: list[bytes] = []
        self._frame_count = 0
        self._index = 0

    def feed(self, frame: bytes) -> str | None:
        self.fed.append(frame)
        self._frame_count += 1
        if self._frame_count % self._frames_per_partial != 0:
            return None
        if self._index >= len(self._partials):
            return None
        partial = self._partials[self._index]
        self._index += 1
        return partial

    def finish(self) -> str:
        text = self._partials[-1] if self._partials else ""
        self.reset()
        return text

    def reset(self) -> None:
        self.fed = []
        self._frame_count = 0
        self._index = 0
