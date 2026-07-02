"""Microphone capture behind an adapter so tests never need real audio.

The real backend (``sounddevice``/PortAudio) is imported lazily; import
failures and permission problems surface as :class:`LocalFlowError`
subclasses with platform-specific fix-it hints.
"""

from __future__ import annotations

import platform
import queue
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator

from local_flow.errors import (
    AudioBackendMissingError,
    MicNotFoundError,
    MicPermissionError,
)

_MIC_PERMISSION_HINTS = {
    "Darwin": "macOS: System Settings -> Privacy & Security -> Microphone, and "
    "enable access for your terminal app; then restart the terminal.",
    "Windows": "Windows: Settings -> Privacy & security -> Microphone -> allow "
    "desktop apps to access your microphone.",
    "Linux": "Linux: check that your user can access the audio device "
    "(PulseAudio/PipeWire running, or membership in the 'audio' group) and that "
    "another app is not holding the microphone exclusively.",
}


def _mic_hint() -> str:
    return _MIC_PERMISSION_HINTS.get(platform.system(), _MIC_PERMISSION_HINTS["Linux"])


class AudioSource(ABC):
    """Produces 16-bit mono PCM frames from somewhere (mic, file, fixture)."""

    sample_rate: int

    @abstractmethod
    def frames(self, frame_ms: int = 30) -> Iterator[bytes]:
        """Yield fixed-size PCM frames until the source stops."""

    @abstractmethod
    def record_until(self, stop: threading.Event, frame_ms: int = 30) -> bytes:
        """Record PCM until ``stop`` is set (push-to-talk), return the buffer."""


class MockAudioSource(AudioSource):
    """Serves a pre-built PCM buffer; used by tests and the headless demo."""

    def __init__(self, pcm: bytes, sample_rate: int = 16000) -> None:
        self.pcm = pcm
        self.sample_rate = sample_rate

    def frames(self, frame_ms: int = 30) -> Iterator[bytes]:
        frame_bytes = int(self.sample_rate * frame_ms / 1000) * 2
        for i in range(0, len(self.pcm), frame_bytes):
            yield self.pcm[i : i + frame_bytes]

    def record_until(self, stop: threading.Event, frame_ms: int = 30) -> bytes:
        return self.pcm


class SounddeviceSource(AudioSource):
    """Live microphone capture via sounddevice/PortAudio (optional extra)."""

    def __init__(self, sample_rate: int = 16000, device: int | str | None = None) -> None:
        try:
            import sounddevice
        except (ImportError, OSError) as exc:
            raise AudioBackendMissingError(
                f"The 'sounddevice' audio backend is unavailable: {exc}",
                hint="Install audio extras: uv sync --extra audio "
                "(Linux also needs the PortAudio system library, e.g. "
                "apt install libportaudio2).",
            ) from exc
        self._sd = sounddevice
        self.sample_rate = sample_rate
        self.device = device
        self._check_input_device()

    def _check_input_device(self) -> None:
        try:
            devices = self._sd.query_devices()
        except Exception as exc:
            raise MicPermissionError(
                f"Could not query audio devices: {exc}", hint=_mic_hint()
            ) from exc
        if not any(d.get("max_input_channels", 0) > 0 for d in devices):
            raise MicNotFoundError(
                "No microphone (input audio device) was found.",
                hint="Plug in or enable a microphone, then check it appears in "
                "your OS sound settings. " + _mic_hint(),
            )

    def _open_stream(self, frame_bytes: int, callback) -> object:
        try:
            return self._sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=frame_bytes // 2,
                device=self.device,
                dtype="int16",
                channels=1,
                callback=callback,
            )
        except self._sd.PortAudioError as exc:
            raise MicPermissionError(
                f"Opening the microphone failed: {exc}",
                hint=_mic_hint(),
            ) from exc

    def frames(self, frame_ms: int = 30) -> Iterator[bytes]:
        frame_bytes = int(self.sample_rate * frame_ms / 1000) * 2
        frame_queue: queue.Queue[bytes] = queue.Queue()

        def callback(indata, _frames, _time, _status) -> None:
            frame_queue.put(bytes(indata))

        stream = self._open_stream(frame_bytes, callback)
        with stream:
            while True:
                yield frame_queue.get()

    def record_until(self, stop: threading.Event, frame_ms: int = 30) -> bytes:
        frame_bytes = int(self.sample_rate * frame_ms / 1000) * 2
        chunks: list[bytes] = []

        def callback(indata, _frames, _time, _status) -> None:
            chunks.append(bytes(indata))

        stream = self._open_stream(frame_bytes, callback)
        with stream:
            while not stop.wait(timeout=0.02):
                pass
        return b"".join(chunks)
