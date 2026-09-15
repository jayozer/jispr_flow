"""Minimal chat-completion interface shared by LM Studio and mocks."""

from __future__ import annotations

from abc import ABC, abstractmethod

Message = dict[str, str]  # {"role": ..., "content": ...}


class ChatClient(ABC):
    """A synchronous chat-completion backend."""

    @property
    def is_transcript_normalizer(self) -> bool:
        """Whether this model requires the specialized transcript-only protocol."""
        return False

    def normalize_transcript(self, transcript: str, *, style: str = "default") -> str:
        """Normalize one bounded transcript with a specialized model."""
        raise NotImplementedError("This client does not support transcript normalization.")

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Send messages, return the assistant reply text (stripped)."""
