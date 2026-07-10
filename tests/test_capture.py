"""`pick_input_device` (pure), `SounddeviceSource` construction-time
device resolution (see the Phase 5 plan, E11: mic priority), and the
`frames()` liveness guard (Group C item 34).
"""

import sys
import threading
import time

import pytest

import local_flow.audio.capture as capture_module
from local_flow.audio.capture import SounddeviceSource, pick_input_device
from local_flow.errors import MicNotFoundError, MicPermissionError


def _device(name: str, max_input_channels: int = 1) -> dict:
    return {"name": name, "max_input_channels": max_input_channels}


class TestPickInputDevice:
    def test_first_matching_preferred_substring_wins(self):
        devices = [_device("MacBook Pro Microphone"), _device("AirPods Pro")]
        assert pick_input_device(devices, ["airpods"]) == 1

    def test_priority_order_beats_device_list_order(self):
        devices = [_device("AirPods Pro"), _device("USB Mic")]
        # "usb" is listed first in the priority list, so it wins even though
        # AirPods appears first in the device list.
        assert pick_input_device(devices, ["usb", "airpods"]) == 1

    def test_case_insensitive_match(self):
        devices = [_device("AirPods Pro")]
        assert pick_input_device(devices, ["AIRPODS"]) == 0

    def test_skips_non_input_devices(self):
        devices = [_device("AirPods Speakers", max_input_channels=0), _device("AirPods Mic")]
        assert pick_input_device(devices, ["airpods"]) == 1

    def test_no_match_returns_none(self):
        devices = [_device("Built-in Microphone")]
        assert pick_input_device(devices, ["airpods"]) is None

    def test_empty_preferred_list_returns_none(self):
        devices = [_device("Built-in Microphone")]
        assert pick_input_device(devices, []) is None

    def test_blank_preferred_entries_are_ignored(self):
        devices = [_device("Built-in Microphone")]
        assert pick_input_device(devices, ["   ", ""]) is None

    def test_second_preferred_entry_used_when_first_has_no_match(self):
        devices = [_device("Built-in Microphone")]
        assert pick_input_device(devices, ["airpods", "built-in"]) == 0


class FakeSounddeviceModule:
    """Minimal stand-in for the real `sounddevice` module."""

    class PortAudioError(Exception):
        pass

    def __init__(self, devices=None, query_error=None):
        self.devices = devices if devices is not None else []
        self.query_error = query_error

    def query_devices(self):
        if self.query_error is not None:
            raise self.query_error
        return self.devices

    def RawInputStream(self, **kwargs):  # pragma: no cover - not exercised here
        raise NotImplementedError


@pytest.fixture
def fake_sounddevice(monkeypatch):
    def _install(devices=None, query_error=None):
        fake = FakeSounddeviceModule(devices=devices, query_error=query_error)
        monkeypatch.setitem(sys.modules, "sounddevice", fake)
        return fake

    return _install


class TestSounddeviceSourceDeviceResolution:
    def test_explicit_device_bypasses_priority_resolution(self, fake_sounddevice):
        fake_sounddevice(devices=[_device("Built-in Microphone")])
        source = SounddeviceSource(device=3, preferred=["airpods"])
        assert source.device == 3
        assert source.chosen_device_name == ""

    def test_no_preferred_uses_system_default(self, fake_sounddevice):
        fake_sounddevice(devices=[_device("Built-in Microphone")])
        source = SounddeviceSource()
        assert source.device is None
        assert source.chosen_device_name == ""

    def test_preferred_match_is_selected(self, fake_sounddevice):
        fake_sounddevice(devices=[_device("Built-in Microphone"), _device("AirPods Pro")])
        source = SounddeviceSource(preferred=["airpods"])
        assert source.device == 1
        assert source.chosen_device_name == "AirPods Pro"

    def test_no_preferred_match_falls_back_to_default(self, fake_sounddevice):
        fake_sounddevice(devices=[_device("Built-in Microphone")])
        source = SounddeviceSource(preferred=["airpods"])
        assert source.device is None
        assert source.chosen_device_name == ""

    def test_earlier_priority_entry_wins_over_later_one(self, fake_sounddevice):
        fake_sounddevice(devices=[_device("USB Mic"), _device("AirPods Pro")])
        source = SounddeviceSource(preferred=["airpods", "usb"])
        assert source.chosen_device_name == "AirPods Pro"

    def test_falls_through_to_next_preferred_entry_when_first_has_no_match(
        self, fake_sounddevice
    ):
        fake_sounddevice(devices=[_device("Built-in Microphone")])
        source = SounddeviceSource(preferred=["airpods", "built-in"])
        assert source.chosen_device_name == "Built-in Microphone"

    def test_no_input_device_at_all_raises_mic_not_found(self, fake_sounddevice):
        fake_sounddevice(devices=[_device("Speakers", max_input_channels=0)])
        with pytest.raises(MicNotFoundError):
            SounddeviceSource()

    def test_query_devices_failure_raises_mic_permission_error(self, fake_sounddevice):
        fake_sounddevice(query_error=RuntimeError("boom"))
        with pytest.raises(MicPermissionError):
            SounddeviceSource()


class _FakeRawInputStream:
    """Stand-in for `sounddevice.RawInputStream`: hands frames to the
    capture callback on demand and lets a test flip `active` off, the way
    PortAudio does when the device disappears mid-session.
    """

    def __init__(self, callback, initial_frames=()):
        self._callback = callback
        self._initial_frames = list(initial_frames)
        self.active = False

    def deliver(self, frame: bytes) -> None:
        self._callback(frame, len(frame) // 2, None, None)

    def __enter__(self):
        self.active = True
        for frame in self._initial_frames:
            self.deliver(frame)
        return self

    def __exit__(self, *exc) -> None:
        self.active = False


class TestFramesLiveness:
    """Group C item 34: `frames()` uses a timed `queue.get` and re-checks the
    stream between waits, so a mid-session mic disconnect (PortAudio stops
    invoking the callback, `active` goes False) ends the iteration instead
    of hanging the hands-free loop forever.
    """

    def _source(self, fake_sounddevice, monkeypatch, initial_frames=()):
        fake = fake_sounddevice(devices=[_device("Built-in Microphone")])
        streams: list[_FakeRawInputStream] = []

        def raw_input_stream(**kwargs):
            stream = _FakeRawInputStream(kwargs["callback"], initial_frames)
            streams.append(stream)
            return stream

        fake.RawInputStream = raw_input_stream
        monkeypatch.setattr(capture_module, "_FRAMES_LIVENESS_TIMEOUT_S", 0.05)
        return SounddeviceSource(), streams

    def test_disconnect_ends_the_iteration_instead_of_hanging(
        self, fake_sounddevice, monkeypatch
    ):
        source, streams = self._source(
            fake_sounddevice, monkeypatch, initial_frames=[b"f1", b"f2"]
        )

        frames = source.frames(30)
        assert next(frames) == b"f1"
        assert next(frames) == b"f2"

        streams[0].active = False  # mic unplugged: no more callbacks, ever
        with pytest.raises(StopIteration):
            next(frames)

    def test_quiet_gap_on_a_live_stream_keeps_waiting(
        self, fake_sounddevice, monkeypatch
    ):
        """A timeout alone must not end the loop: only inactivity does. A
        frame delivered a few liveness-timeouts late is still yielded.
        """
        source, streams = self._source(fake_sounddevice, monkeypatch)
        frames = source.frames(30)

        def deliver_late() -> None:
            # The stream only exists once `next(frames)` starts the
            # generator; wait for it, then let a few 0.05s liveness
            # timeouts elapse before delivering.
            deadline = time.monotonic() + 2
            while not streams and time.monotonic() < deadline:
                time.sleep(0.005)
            time.sleep(0.15)
            streams[0].deliver(b"late")

        threading.Thread(target=deliver_late, daemon=True).start()
        assert next(frames) == b"late"

    def test_frames_are_forwarded_to_optional_level_callback(
        self, fake_sounddevice, monkeypatch
    ):
        source, _streams = self._source(
            fake_sounddevice, monkeypatch, initial_frames=[b"f1", b"f2"]
        )
        observed = []
        source.set_level_callback(observed.append)

        frames = source.frames(30)
        assert next(frames) == b"f1"
        assert next(frames) == b"f2"
        assert observed == [b"f1", b"f2"]

    def test_broken_level_callback_does_not_interrupt_capture(
        self, fake_sounddevice, monkeypatch
    ):
        source, _streams = self._source(
            fake_sounddevice, monkeypatch, initial_frames=[b"audio"]
        )
        source.set_level_callback(
            lambda _frame: (_ for _ in ()).throw(RuntimeError("broken pill"))
        )

        assert next(source.frames(30)) == b"audio"
