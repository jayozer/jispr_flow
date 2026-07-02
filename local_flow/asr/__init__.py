"""Local speech-to-text adapters. LM Studio is never used for ASR."""

from local_flow.asr.base import Transcriber
from local_flow.asr.mock import MockTranscriber

__all__ = ["MockTranscriber", "Transcriber"]
