"""LM Studio client using its OpenAI-compatible HTTP API.

local-flow is local-first: this client talks only to a local (or explicitly
configured LAN) LM Studio server and refuses known cloud AI endpoints.
LM Studio is used for text polish and command mode only — never for ASR.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from local_flow.config import DEFAULT_LMSTUDIO_BASE_URL
from local_flow.errors import (
    ConfigError,
    LMStudioConnectionError,
    LMStudioModelError,
    LMStudioResponseError,
)
from local_flow.llm.base import ChatClient, Message

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_BLOCKED_HOST_SUFFIXES = (
    "openai.com",
    "anthropic.com",
    "wisprflow.ai",
    "flowvoice.ai",
    "googleapis.com",
    "azure.com",
)

_CONNECT_HINT = (
    "Start LM Studio, load a model, then enable the local server "
    "(Developer tab -> Start Server). The default address is "
    f"{DEFAULT_LMSTUDIO_BASE_URL}; override with LOCAL_FLOW_LMSTUDIO_BASE_URL."
)


@dataclass(frozen=True)
class StreamResult:
    """One streamed completion plus timing measured at the HTTP boundary."""

    text: str
    first_token_s: float
    total_s: float


class LMStudioClient(ChatClient):
    def __init__(
        self,
        base_url: str = DEFAULT_LMSTUDIO_BASE_URL,
        model: str = "",
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        host = (urlparse(self.base_url).hostname or "").lower()
        if any(host == s or host.endswith("." + s) for s in _BLOCKED_HOST_SUFFIXES):
            raise ConfigError(
                f"Refusing to use cloud AI endpoint {base_url!r}: local-flow is local-first.",
                hint="Point LOCAL_FLOW_LMSTUDIO_BASE_URL at a local LM Studio server, "
                f"e.g. {DEFAULT_LMSTUDIO_BASE_URL}.",
            )
        if host not in _LOCAL_HOSTS:
            logger.warning(
                "LM Studio base URL %s is not localhost; make sure this is a "
                "machine you trust on your own network.",
                base_url,
            )
        self._client = httpx.Client(timeout=timeout, transport=transport)
        # True once `resolve_model` auto-picked `self.model` (as opposed to a
        # user-configured id): only auto-picked ids may be dropped and
        # re-listed when they go stale (see `chat`).
        self._auto_picked = False

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def list_models(self) -> list[str]:
        """Return the ids of models currently available in LM Studio."""
        try:
            response = self._client.get(self._url("models"))
        except httpx.TimeoutException as exc:
            raise LMStudioConnectionError(
                f"Timed out reaching LM Studio at {self.base_url}.", hint=_CONNECT_HINT
            ) from exc
        except httpx.HTTPError as exc:
            raise LMStudioConnectionError(
                f"Could not reach LM Studio at {self.base_url}: {exc}", hint=_CONNECT_HINT
            ) from exc
        data = self._parse_json(response)
        return [item.get("id", "") for item in data.get("data", []) if isinstance(item, dict)]

    def resolve_model(self) -> str:
        """Return the configured model, or auto-pick the first loaded one."""
        if self.model:
            return self.model
        models = self.list_models()
        if not models:
            raise LMStudioModelError(
                f"LM Studio at {self.base_url} has no model loaded.",
                hint="In LM Studio, download a model (e.g. Qwen2.5 7B Instruct) and load "
                "it, or set LOCAL_FLOW_LMSTUDIO_MODEL to a model identifier.",
            )
        self.model = models[0]
        self._auto_picked = True
        return self.model

    def chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        response = self._post_chat(messages, temperature, max_tokens)
        if response.status_code == 404 and self._auto_picked:
            # The auto-picked model id has gone stale (the user swapped
            # models in LM Studio); drop it, re-list, and retry once rather
            # than 404 on every call until restart. A user-configured model
            # is never substituted -- the 404 below stays actionable.
            self.model = ""
            response = self._post_chat(messages, temperature, max_tokens)

        if response.status_code == 404:
            raise LMStudioModelError(
                f"LM Studio does not know the model {self.model!r} "
                f"(HTTP 404 from {self.base_url}).",
                hint="Check the model identifier in LM Studio (My Models) and set "
                "LOCAL_FLOW_LMSTUDIO_MODEL to match, or leave it empty to auto-pick.",
            )
        if response.status_code >= 400:
            detail = _error_detail(response)
            raise LMStudioResponseError(
                f"LM Studio returned HTTP {response.status_code}: {detail}",
                hint="Check the LM Studio server logs (Developer tab) for details.",
            )

        data = self._parse_json(response)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LMStudioResponseError(
                f"LM Studio response from {self.base_url} contained no choices.",
                hint="Make sure a chat-capable (instruct) model is loaded.",
            )
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LMStudioResponseError(
                "LM Studio response was missing message content.",
                hint="Make sure a chat-capable (instruct) model is loaded.",
            )
        return content.strip()

    def chat_stream(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> StreamResult:
        """Stream a local completion and report first-token/total latency.

        The production polisher intentionally continues to use :meth:`chat`
        and inserts only complete responses. This method exists for the model
        benchmark and consumes OpenAI-compatible ``data:`` SSE events.
        """
        payload: dict[str, object] = {
            "model": self.resolve_model(),
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        started = clock()
        try:
            request = self._client.build_request(
                "POST", self._url("chat/completions"), json=payload
            )
            response = self._client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise LMStudioConnectionError(
                f"Timed out waiting for LM Studio at {self.base_url} "
                f"(model {self.model!r}).",
                hint=_CONNECT_HINT,
            ) from exc
        except httpx.HTTPError as exc:
            raise LMStudioConnectionError(
                f"Could not reach LM Studio at {self.base_url}: {exc}", hint=_CONNECT_HINT
            ) from exc

        try:
            if response.status_code == 404:
                raise LMStudioModelError(
                    f"LM Studio does not know the model {self.model!r} "
                    f"(HTTP 404 from {self.base_url}).",
                    hint="Load the exact benchmark model in LM Studio or update its model id.",
                )
            if response.status_code >= 400:
                response.read()
                raise LMStudioResponseError(
                    f"LM Studio returned HTTP {response.status_code}: {_error_detail(response)}",
                    hint="Check the LM Studio server logs (Developer tab) for details.",
                )

            chunks: list[str] = []
            first_token_s: float | None = None
            for line in response.iter_lines():
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    event = json.loads(raw)
                except ValueError as exc:
                    raise LMStudioResponseError(
                        "LM Studio returned malformed streaming JSON."
                    ) from exc
                choices = event.get("choices") if isinstance(event, dict) else None
                choice = choices[0] if isinstance(choices, list) and choices else None
                delta = choice.get("delta") if isinstance(choice, dict) else None
                content = delta.get("content") if isinstance(delta, dict) else None
                if isinstance(content, str) and content:
                    if first_token_s is None:
                        first_token_s = clock() - started
                    chunks.append(content)
            total_s = clock() - started
        finally:
            response.close()

        text = "".join(chunks).strip()
        if first_token_s is None:
            raise LMStudioResponseError(
                "LM Studio streaming response contained no text tokens."
            )
        return StreamResult(text=text, first_token_s=first_token_s, total_s=total_s)

    def _post_chat(
        self,
        messages: list[Message],
        temperature: float,
        max_tokens: int | None,
    ) -> httpx.Response:
        payload: dict[str, object] = {
            "model": self.resolve_model(),
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        try:
            return self._client.post(self._url("chat/completions"), json=payload)
        except httpx.TimeoutException as exc:
            raise LMStudioConnectionError(
                f"Timed out waiting for LM Studio at {self.base_url} "
                f"(model {self.model!r}).",
                hint="A slow or overloaded model can exceed the timeout; raise "
                "LOCAL_FLOW_LMSTUDIO_TIMEOUT or load a smaller model.",
            ) from exc
        except httpx.HTTPError as exc:
            raise LMStudioConnectionError(
                f"Could not reach LM Studio at {self.base_url}: {exc}", hint=_CONNECT_HINT
            ) from exc

    def _parse_json(self, response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise LMStudioResponseError(
                f"LM Studio at {self.base_url} returned a non-JSON response "
                f"(HTTP {response.status_code}).",
                hint="Verify the base URL points at the OpenAI-compatible endpoint, "
                "usually ending in /v1.",
            ) from exc
        if not isinstance(data, dict):
            raise LMStudioResponseError("LM Studio returned unexpected JSON (not an object).")
        return data

    def close(self) -> None:
        self._client.close()


def _error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
            if isinstance(error, str):
                return error
    except ValueError:
        pass
    return response.text[:200] or "(empty body)"
