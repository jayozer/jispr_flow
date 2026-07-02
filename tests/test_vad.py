"""Energy VAD and speech segmentation, all with synthetic PCM."""

from local_flow.audio.vad import EnergyVAD, MockVAD, rms, split_segments
from local_flow.demo import synth_pcm

SR = 16000


class TestEnergyVAD:
    def test_silence_is_not_speech(self):
        vad = EnergyVAD(threshold=500)
        silence = b"\x00\x00" * 480
        assert vad.is_speech(silence, SR) is False

    def test_tone_is_speech(self):
        vad = EnergyVAD(threshold=500)
        tone = synth_pcm([(30, 12000)])
        assert vad.is_speech(tone, SR) is True

    def test_rms_of_silence_is_zero(self):
        assert rms(b"\x00\x00" * 100) == 0.0


class TestSplitSegments:
    def test_two_bursts_give_two_segments(self):
        pcm = synth_pcm([(200, 0), (600, 12000), (800, 0), (600, 12000), (200, 0)])
        segments = split_segments(pcm, SR, EnergyVAD(500), silence_ms=400)
        assert len(segments) == 2

    def test_short_blip_is_dropped(self):
        pcm = synth_pcm([(200, 0), (30, 12000), (800, 0)])
        segments = split_segments(pcm, SR, EnergyVAD(500), silence_ms=400, min_speech_ms=90)
        assert segments == []

    def test_pure_silence_gives_no_segments(self):
        pcm = synth_pcm([(1000, 0)])
        assert split_segments(pcm, SR, EnergyVAD(500)) == []

    def test_mock_vad_scripting(self):
        # 10 frames: 4 speech, then silence long enough to close the segment.
        frame = b"\x00\x00" * 480
        pcm = frame * 10
        vad = MockVAD([True, True, True, True] + [False] * 6)
        segments = split_segments(pcm, SR, vad, frame_ms=30, silence_ms=90)
        assert len(segments) == 1
