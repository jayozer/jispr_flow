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

# How long `SounddeviceSource.frames()` waits for the next mic frame before
# re-checking that the stream is still alive. Frames normally arrive every
# `frame_ms` (~30ms) even during silence, so hitting this at all already
# means something is wrong with the device. Module-level so tests can
# monkeypatch it down instead of sleeping a real second.
_FRAMES_LIVENESS_TIMEOUT_S = 1.0

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


def pick_input_device(devices: list[dict], preferred: list[str]) -> int | None:
    """Return the index of the best input device matching ``preferred``.

    ``devices`` is a sounddevice-style list of dicts (``"name"``,
    ``"max_input_channels"``, ...) as returned by ``sd.query_devices()``.
    ``preferred`` is a priority-ordered list of case-insensitive name
    substrings (see ``local_flow.app.parse_mic_priority``): each entry is
    tried in order, and the first input-capable device (``max_input_channels
    > 0``) whose name contains it wins -- so priority is about *preference
    order*, not device list order. Blank entries are ignored. Returns
    ``None`` when nothing matches (or ``preferred`` is empty), meaning "use
    the system default".
    """
    for pref in preferred:
        needle = pref.strip().lower()
        if not needle:
            continue
        for index, device in enumerate(devices):
            if device.get("max_input_channels", 0) <= 0:
                continue
            if needle in str(device.get("name", "")).lower():
                return index
    return None


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
    """Live microphone capture via sounddevice/PortAudio (optional extra).

    When ``device`` is given explicitly, it is used as-is (unchanged
    behavior). Otherwise, ``preferred`` (priority-ordered name substrings --
    see ``local_flow.app.parse_mic_priority``) is resolved against the queried
    device list at construction time via ``pick_input_device``: each priority
    entry is tried in turn, skipping any candidate that (on re-check) turns
    out not to be input-capable, until one is accepted or the list is
    exhausted -- then the system default (``device=None``) is used.
    ``chosen_device_name`` records what was picked (``""`` for the system
    default), for ``local-flow check`` to display.

    This resolution only ever inspects the queried device list -- it never
    opens a real stream, so it cannot detect a device that is *listed* but
    fails to actually open (in-use, unplugged mid-session, driver error).
    That deeper, open-and-verify fallback is explicitly out of scope for v1
    (see the Phase 5 plan); the existing errors (``MicPermissionError``,
    ``MicNotFoundError``) still fire exactly as before when the device query
    itself fails or no input device exists at all.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        device: int | str | None = None,
        preferred: list[str] | None = None,
    ) -> None:
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
        self.preferred = list(preferred) if preferred else []

        if device is not None:
            self.device = device
            self.chosen_device_name = ""
            self._check_input_device()
        else:
            devices = self._check_input_device()
            self.device, self.chosen_device_name = self._resolve_device(devices)

    def _check_input_device(self) -> list[dict]:
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
        return devices

    def _resolve_device(self, devices: list[dict]) -> tuple[int | None, str]:
        """Pick a device index/name from ``self.preferred``, trying each
        priority match in turn (skipping any that isn't actually
        input-capable) before falling back to the system default.
        """
        tried: set[int] = set()
        for pref in self.preferred:
            index = pick_input_device(devices, [pref])
            if index is None or index in tried:
                continue
            tried.add(index)
            if devices[index].get("max_input_channels", 0) > 0:
                return index, str(devices[index].get("name", ""))
        return None, ""

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
                # Timed get, not a bare blocking one: when the device dies
                # mid-session (mic unplugged, Bluetooth dropout) PortAudio
                # simply stops invoking `callback`, and a blocking `get()`
                # would hang this generator -- and the whole hands-free
                # loop, including its `stop_event` check -- forever. On a
                # quiet queue, end the iteration once the stream reports
                # itself no longer active.
                try:
                    yield frame_queue.get(timeout=_FRAMES_LIVENESS_TIMEOUT_S)
                except queue.Empty:
                    if not getattr(stream, "active", True):
                        return

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
