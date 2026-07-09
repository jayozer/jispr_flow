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
    relative to real time).

    The buffer is bounded to a trailing ``window_s`` seconds (default 30,
    Whisper's native chunk size), and a re-transcription that comes back
    blank drops the buffer entirely: the window was silence, and keeping it
    would only make the next re-transcription slower. Without both, an
    utterance that never closes (continuous background noise below the
    segmenter's threshold, or plain silence between utterances -- the caller
    only ``reset()``s after a segment yields) grows the buffer without limit
    (~115 MB/h at 16 kHz) and each synchronous ``feed()`` re-runs Whisper
    over all of it. Preview text is display-only (see the module docstring),
    so a partial that covers only the trailing window is an acceptable
    trade for a session that stays responsive.
    """

    def __init__(
        self,
        transcriber: Transcriber,
        sample_rate: int,
        interval_ms: int = 1000,
        window_s: float = 30.0,
    ) -> None:
        self._transcriber = transcriber
        self._sample_rate = sample_rate
        # 16-bit mono PCM: 2 bytes/sample, same arithmetic `segment_stream`
        # uses for `frame_ms` -> byte-length elsewhere in the codebase.
        self._interval_bytes = int(sample_rate * interval_ms / 1000) * 2
        self._window_bytes = int(sample_rate * window_s) * 2
        self._buffer = bytearray()
        self._new_bytes = 0  # bytes fed since the last (re-)transcription

    def feed(self, frame: bytes) -> str | None:
        self._buffer.extend(frame)
        if len(self._buffer) > self._window_bytes:
            del self._buffer[: len(self._buffer) - self._window_bytes]
        self._new_bytes += len(frame)
        if self._new_bytes < self._interval_bytes:
            return None
        self._new_bytes = 0
        text = self._transcriber.transcribe(bytes(self._buffer), self._sample_rate)
        if not text.strip():
            # The whole window transcribed to nothing: silence. Drop it so
            # idle time between utterances can't accumulate.
            self.reset()
        return text

    def finish(self) -> str:
        text = self._transcriber.transcribe(bytes(self._buffer), self._sample_rate)
        self.reset()
        return text

    def reset(self) -> None:
        self._buffer = bytearray()
        self._new_bytes = 0
