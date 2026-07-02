"""Mock chat client for tests and the headless demo."""

from __future__ import annotations

from collections.abc import Callable

from local_flow.llm.base import ChatClient, Message


class MockChatClient(ChatClient):
    """Replays queued responses (or calls a handler) and records requests.

    ``responses`` may be a list of canned replies consumed in order, or a
    callable receiving the message list and returning the reply. When a
    response list is exhausted, the last entry is repeated.
    """

    def __init__(
        self,
        responses: list[str] | Callable[[list[Message]], str] | None = None,
    ) -> None:
        self._handler = responses if callable(responses) else None
        self._queue = list(responses) if isinstance(responses, list) else []
        self.requests: list[list[Message]] = []

    def chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        self.requests.append(messages)
        if self._handler is not None:
            return self._handler(messages).strip()
        if self._queue:
            reply = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
            return reply.strip()
        # Default: echo the last user message so pipelines stay runnable.
        for message in reversed(messages):
            if message.get("role") == "user":
                return message.get("content", "").strip()
        return ""
