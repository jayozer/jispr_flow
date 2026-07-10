"""Pure PCM level calculation for the floating recording pill."""

from __future__ import annotations

import math


def pcm_level(pcm: bytes) -> float:
    """Return a perceptual 0..1 level for little-endian signed 16-bit PCM.

    RMS is mapped from -50 dBFS to 0 dBFS so quiet speech still moves the
    meter while normal room noise stays near zero. Odd trailing bytes are
    ignored, matching the ASR adapters' PCM handling.
    """
    usable = len(pcm) // 2 * 2
    if not usable:
        return 0.0
    samples = memoryview(pcm[:usable]).cast("h")
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    if mean_square <= 0:
        return 0.0
    rms = math.sqrt(mean_square) / 32768.0
    dbfs = 20.0 * math.log10(max(rms, 1e-8))
    return max(0.0, min(1.0, (dbfs + 50.0) / 50.0))
