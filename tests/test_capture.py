"""`pick_input_device` (pure) and `SounddeviceSource` construction-time
device resolution -- see the Phase 5 plan (E11: mic priority).
"""

import sys

import pytest

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
