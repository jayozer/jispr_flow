"""Voice activity detection behind a small adapter interface.

Backends:
- :class:`EnergyVAD` — pure-Python RMS threshold, always available.
- :class:`WebRtcVAD` — wraps the optional ``webrtcvad`` package.
- :class:`MockVAD` — scripted answers for tests.

Audio is 16-bit signed little-endian mono PCM throughout.
"""

from __future__ import annotations

import array
import math
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator

from local_flow.errors import VADBackendMissingError


def rms(frame: bytes) -> float:
    """Root-mean-square amplitude of a 16-bit PCM frame."""
    samples = array.array("h")
    samples.frombytes(frame[: len(frame) // 2 * 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class VoiceActivityDetector(ABC):
    @abstractmethod
    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        """True if the PCM frame contains speech."""


class EnergyVAD(VoiceActivityDetector):
    """Dependency-free VAD: a frame is speech when its RMS crosses a threshold."""

    def __init__(self, threshold: float = 500.0) -> None:
        self.threshold = threshold

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        return rms(frame) >= self.threshold


class WebRtcVAD(VoiceActivityDetector):
    """WebRTC VAD (optional dependency; frames must be 10/20/30 ms)."""

    def __init__(self, aggressiveness: int = 2) -> None:
        try:
            import webrtcvad
        except ImportError as exc:
            raise VADBackendMissingError(
                "The 'webrtcvad' package is not installed.",
                hint="Install audio extras: uv sync --extra audio "
                "(or set LOCAL_FLOW_VAD_BACKEND=energy).",
            ) from exc
        self._vad = webrtcvad.Vad(aggressiveness)

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        return bool(self._vad.is_speech(frame, sample_rate))


class MockVAD(VoiceActivityDetector):
    """Replays a scripted sequence of speech/silence decisions."""

    def __init__(self, decisions: Iterable[bool]) -> None:
        self._decisions = list(decisions)
        self._index = 0

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        if self._index < len(self._decisions):
            decision = self._decisions[self._index]
            self._index += 1
            return decision
        return False


def segment_stream(
    frames: Iterable[bytes],
    vad: VoiceActivityDetector,
    sample_rate: int,
    frame_ms: int = 30,
    silence_ms: int = 600,
    min_speech_ms: int = 90,
) -> Iterator[bytes]:
    """Group a stream of fixed-size frames into speech segments.

    A segment opens on the first speech frame and closes after ``silence_ms``
    of continuous non-speech. Segments shorter than ``min_speech_ms`` of
    speech are dropped as noise blips. Works on any frame iterable, so it is
    fully testable without a microphone.
    """
    max_silence_frames = max(1, silence_ms // frame_ms)
    min_speech_frames = max(1, min_speech_ms // frame_ms)

    buffer: list[bytes] = []
    speech_frames = 0
    silence_run = 0

    def finish() -> bytes | None:
        nonlocal buffer, speech_frames, silence_run
        segment = b"".join(buffer)
        keep = speech_frames >= min_speech_frames
        buffer, speech_frames, silence_run = [], 0, 0
        return segment if keep else None

    for frame in frames:
        speaking = vad.is_speech(frame, sample_rate)
        if speaking:
            buffer.append(frame)
            speech_frames += 1
            silence_run = 0
        elif buffer:
            buffer.append(frame)
            silence_run += 1
            if silence_run >= max_silence_frames:
                segment = finish()
                if segment is not None:
                    yield segment
    if buffer:
        segment = finish()
        if segment is not None:
            yield segment


def split_segments(
    pcm: bytes,
    sample_rate: int,
    vad: VoiceActivityDetector,
    frame_ms: int = 30,
    silence_ms: int = 600,
    min_speech_ms: int = 90,
) -> list[bytes]:
    """Split a complete PCM buffer into speech segments using ``segment_stream``."""
    frame_bytes = int(sample_rate * frame_ms / 1000) * 2
    frames = (pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes))
    return list(
        segment_stream(
            frames,
            vad,
            sample_rate,
            frame_ms=frame_ms,
            silence_ms=silence_ms,
            min_speech_ms=min_speech_ms,
        )
    )
