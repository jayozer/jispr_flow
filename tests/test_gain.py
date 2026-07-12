"""`normalize_peak` -- pure int16 PCM peak normalization used for
`vad_preset="whisper"` (see the Phase 5 plan, E11).
"""

import array

from local_flow.audio.gain import normalize_peak, peak_amplitude


def _pcm(samples: list[int]) -> bytes:
    return array.array("h", samples).tobytes()


def _samples(pcm: bytes) -> list[int]:
    arr = array.array("h")
    arr.frombytes(pcm)
    return list(arr)


class TestNormalizePeak:
    def test_scales_peak_to_target_fraction_of_int16_max(self):
        pcm = _pcm([100, -200, 150])
        out = normalize_peak(pcm, target=0.9)
        scaled = _samples(out)
        # Peak sample (-200) should now be at -0.9 * 32767, rounded.
        assert scaled[1] == round(-0.9 * 32767)
        # Everything scales by the same factor.
        factor = scaled[1] / -200
        assert scaled[0] == round(100 * factor)
        assert scaled[2] == round(150 * factor)

    def test_default_target_is_point_nine(self):
        pcm = _pcm([1000, -500])
        out = normalize_peak(pcm)
        scaled = _samples(out)
        assert scaled[0] == round(0.9 * 32767)

    def test_silence_all_zero_returned_unchanged(self):
        pcm = _pcm([0, 0, 0, 0])
        assert normalize_peak(pcm) == pcm

    def test_empty_bytes_returned_unchanged(self):
        assert normalize_peak(b"") == b""

    def test_odd_trailing_byte_is_preserved_after_scaled_samples(self):
        pcm = _pcm([100, -200]) + b"\x7f"
        out = normalize_peak(pcm, target=0.9)
        assert out[-1:] == b"\x7f"
        assert len(out) == len(pcm)
        scaled = _samples(out[:-1])
        assert scaled == _samples(normalize_peak(_pcm([100, -200]), target=0.9))

    def test_lone_odd_byte_with_no_full_sample_is_unchanged(self):
        pcm = b"\x01"
        assert normalize_peak(pcm) == pcm

    def test_already_at_target_peak_is_a_near_identity_scale(self):
        peak = round(0.9 * 32767)
        pcm = _pcm([peak, -peak])
        out = normalize_peak(pcm, target=0.9)
        scaled = _samples(out)
        assert scaled == [peak, -peak]

    def test_amplifying_beyond_full_scale_clamps_to_int16_range(self):
        # target > 1.0 would ask for a peak beyond what int16 can hold;
        # the result must clamp rather than wrap/overflow.
        pcm = _pcm([32767, -32768, 16000])
        out = normalize_peak(pcm, target=1.5)
        scaled = _samples(out)
        assert max(scaled) <= 32767
        assert min(scaled) >= -32768
        # The original peak magnitude sample (-32768) should clamp exactly
        # to the int16 floor.
        assert scaled[1] == -32768

    def test_quiet_whisper_like_audio_is_boosted_well_above_input(self):
        pcm = _pcm([50, -80, 60, -40])
        out = normalize_peak(pcm, target=0.9)
        scaled = _samples(out)
        assert max(abs(s) for s in scaled) > max(abs(s) for s in _samples(pcm)) * 100


class TestPeakAmplitude:
    def test_returns_largest_absolute_sample(self):
        assert peak_amplitude(_pcm([12, -345, 200])) == 345

    def test_empty_and_odd_only_input_are_silent(self):
        assert peak_amplitude(b"") == 0
        assert peak_amplitude(b"\x7f") == 0

    def test_max_gain_prevents_background_noise_from_reaching_full_scale(self):
        pcm = _pcm([50, -80, 60, -40])

        out = normalize_peak(pcm, target=0.9, max_gain=24.0)

        assert _samples(out) == [1200, -1920, 1440, -960]
