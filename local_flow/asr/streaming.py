"""Live-preview transcription: windowed re-transcription of the in-progress
utterance while the user is still speaking.

Preview text produced here is display-only. The *final*, inserted text for
an utterance always comes from the normal per-segment
``transcriber.transcribe()`` call already made by the pipeline (see
``local_flow.app._handle_utterance``) -- this module never feeds back into
that path.
"""

from __future__ import annotations

from typing import Protocol

from local_flow.asr.base import Transcriber


class TranscriberStream(Protocol):
    """Incremental re-transcription of a single, still-open utterance."""

    def feed(self, frame: bytes) -> str | None:
        """Buffer one PCM frame; return re-transcribed partial text, or None."""
        ...

    def finish(self) -> str:
        """Return final text for all buffered audio so far, then reset."""
        ...

    def reset(self) -> None:
        """Drop any buffered audio without transcribing it."""
        ...


class WindowedStream:
    """Re-transcribes the accumulated utterance every ``interval_ms`` of new audio.

    ``feed()`` runs the transcription synchronously on the calling thread;
    mic frames buffer in the source's queue meanwhile (documented tradeoff --
    a slow transcription call can make the next few frames arrive "late"
    relative to real time, but nothing is dropped).
    """

    def __init__(
        self, transcriber: Transcriber, sample_rate: int, interval_ms: int = 1000
    ) -> None:
        self._transcriber = transcriber
        self._sample_rate = sample_rate
        # 16-bit mono PCM: 2 bytes/sample, same arithmetic `segment_stream`
        # uses for `frame_ms` -> byte-length elsewhere in the codebase.
        self._interval_bytes = int(sample_rate * interval_ms / 1000) * 2
        self._buffer = bytearray()
        self._new_bytes = 0  # bytes fed since the last (re-)transcription

    def feed(self, frame: bytes) -> str | None:
        self._buffer.extend(frame)
        self._new_bytes += len(frame)
        if self._new_bytes < self._interval_bytes:
            return None
        self._new_bytes = 0
        return self._transcriber.transcribe(bytes(self._buffer), self._sample_rate)

    def finish(self) -> str:
        text = self._transcriber.transcribe(bytes(self._buffer), self._sample_rate)
        self.reset()
        return text

    def reset(self) -> None:
        self._buffer = bytearray()
        self._new_bytes = 0
