"""Crash-safe pending-audio autosave.

``PendingAudioStore`` writes an utterance's raw PCM to a WAV file under
``data_dir/pending/`` *before* the pipeline processes it. If the process
crashes (or is force-quit) mid-dictation, the WAV survives on disk and
``local-flow recover`` can replay it through the pipeline later. On a normal
successful run the caller deletes the file right away, so ``pending/`` is
empty in the common case and only ever holds genuinely lost utterances.

Filenames are ``uuid4().hex`` -- no wall-clock read is needed for uniqueness
or ordering, so ``save`` has no clock dependency at all.
"""

from __future__ import annotations

import uuid
import wave
from pathlib import Path


class PendingAudioStore:
    """Persists in-flight utterance PCM as WAV files under ``data_dir/pending/``."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    @property
    def pending_dir(self) -> Path:
        return self.data_dir / "pending"

    def save(self, pcm: bytes, sample_rate: int) -> Path:
        """Write ``pcm`` (16-bit mono) to a new WAV file; return its path."""
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        path = self.pending_dir / f"{uuid.uuid4().hex}.wav"
        with wave.open(str(path), "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(sample_rate)
            fh.writeframes(pcm)
        return path

    def delete(self, path: Path) -> None:
        """Remove a pending WAV file; a no-op if it is already gone."""
        Path(path).unlink(missing_ok=True)

    def pending(self) -> list[Path]:
        """List pending WAV files, sorted by name for deterministic order.

        Returns an empty list (rather than raising) when ``pending/`` does
        not exist yet, which is the common case before any crash has ever
        happened.
        """
        if not self.pending_dir.is_dir():
            return []
        return sorted(self.pending_dir.glob("*.wav"))

    def load(self, path: Path) -> tuple[bytes, int]:
        """Read a WAV file back into ``(pcm_bytes, sample_rate)``.

        Raises :class:`ValueError` if the file cannot be read as a WAV
        (corrupt or truncated) so callers processing a batch of pending
        files (see ``local-flow recover``) can skip just that one file
        instead of aborting the whole recovery run.
        """
        try:
            with wave.open(str(path), "rb") as fh:
                sample_rate = fh.getframerate()
                pcm = fh.readframes(fh.getnframes())
        except Exception as exc:  # any bad file becomes a plain ValueError
            raise ValueError(f"could not read WAV file {path}: {exc}") from exc
        return pcm, sample_rate
