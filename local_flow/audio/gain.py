"""Peak (gain) normalization for 16-bit PCM audio.

Pure stdlib (``array`` only, no numpy) -- used to boost quiet/whispered
dictation before ASR when ``vad_preset="whisper"`` (see
``local_flow.app._handle_utterance``).
"""

from __future__ import annotations

import array

_INT16_MIN = -32768
_INT16_MAX = 32767


def peak_amplitude(pcm: bytes) -> int:
    """Return the largest absolute int16 sample in ``pcm``.

    This is intentionally an utterance-level electrical-silence check, not
    voice activity detection: it has no frame threshold or minimum duration.
    """
    usable = len(pcm) // 2 * 2
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    return max((abs(sample) for sample in samples), default=0)


def normalize_peak(
    pcm: bytes,
    target: float = 0.9,
    *,
    max_gain: float | None = None,
) -> bytes:
    """Scale 16-bit signed little-endian mono PCM so its peak sample hits
    ``target * 32767``.

    - Empty input, or input with no full 16-bit sample (a lone odd trailing
      byte), is returned unchanged.
    - Silence (peak amplitude ``0``) is returned unchanged -- there is
      nothing to scale against.
    - A trailing odd byte that doesn't form a whole sample is preserved
      as-is at the end of the output (same odd-length tolerance as
      ``local_flow.audio.vad.rms``), so the output is never shorter than a
      malformed input minus a fraction of a sample.
    - ``max_gain`` optionally caps amplification so low-level background
      noise cannot be promoted to full scale. ``None`` preserves the original
      unrestricted behavior.
    - Scaled samples are clamped to the int16 range in case rounding (or a
      ``target`` >= 1.0) would otherwise overflow it.
    """
    usable = len(pcm) // 2 * 2
    trailing = pcm[usable:]
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    if not samples:
        return pcm

    peak = max(abs(sample) for sample in samples)
    if peak == 0:
        return pcm

    scale = (target * _INT16_MAX) / peak
    if max_gain is not None:
        scale = min(scale, max(0.0, max_gain))
    scaled = array.array(
        "h",
        (max(_INT16_MIN, min(_INT16_MAX, round(s * scale))) for s in samples),
    )
    return scaled.tobytes() + trailing
