"""Minimal chat-completion interface shared by LM Studio and mocks."""

from __future__ import annotations

from abc import ABC, abstractmethod

Message = dict[str, str]  # {"role": ..., "content": ...}


class ChatClient(ABC):
    """A synchronous chat-completion backend."""

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Send messages, return the assistant reply text (stripped)."""
