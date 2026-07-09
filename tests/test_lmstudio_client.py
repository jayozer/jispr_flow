"""LM Studio client behaviour, exercised through httpx.MockTransport."""

import httpx
import pytest

from local_flow.errors import (
    ConfigError,
    LMStudioConnectionError,
    LMStudioModelError,
    LMStudioResponseError,
)
from local_flow.llm.lmstudio import LMStudioClient

BASE = "http://localhost:1234/v1"


def make_client(handler, model="test-model") -> LMStudioClient:
    return LMStudioClient(BASE, model=model, transport=httpx.MockTransport(handler))


def chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": content}}]}
    )


class TestChatSuccess:
    def test_returns_stripped_content(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            return chat_response("  polished text \n")

        client = make_client(handler)
        assert client.chat([{"role": "user", "content": "hi"}]) == "polished text"

    def test_sends_model_and_messages(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen.update(json.loads(request.content))
            return chat_response("ok")

        make_client(handler).chat([{"role": "user", "content": "hello"}])
        assert seen["model"] == "test-model"
        assert seen["messages"] == [{"role": "user", "content": "hello"}]
        assert seen["stream"] is False

    def test_auto_picks_first_loaded_model(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": "qwen2.5-7b"}]})
            return chat_response("ok")

        client = make_client(handler, model="")
        assert client.chat([{"role": "user", "content": "x"}]) == "ok"
        assert client.model == "qwen2.5-7b"


class TestErrorHandling:
    def test_server_down_raises_connection_error_with_hint(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(LMStudioConnectionError) as excinfo:
            make_client(handler).chat([{"role": "user", "content": "x"}])
        message = str(excinfo.value)
        assert "localhost:1234" in message
        assert "Start LM Studio" in message

    def test_timeout_raises_connection_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        with pytest.raises(LMStudioConnectionError, match="Timed out"):
            make_client(handler).chat([{"role": "user", "content": "x"}])

    def test_unknown_model_raises_model_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "model not found"})

        with pytest.raises(LMStudioModelError, match="test-model"):
            make_client(handler).chat([{"role": "user", "content": "x"}])

    def test_no_model_loaded_raises_model_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        with pytest.raises(LMStudioModelError, match="no model loaded"):
            make_client(handler, model="").chat([{"role": "user", "content": "x"}])

    def test_http_500_raises_response_error_with_detail(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": {"message": "kaboom"}})

        with pytest.raises(LMStudioResponseError, match="kaboom"):
            make_client(handler).chat([{"role": "user", "content": "x"}])

    def test_non_json_response_raises_response_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not the api</html>")

        with pytest.raises(LMStudioResponseError, match="non-JSON"):
            make_client(handler).chat([{"role": "user", "content": "x"}])

    def test_missing_choices_raises_response_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": []})

        with pytest.raises(LMStudioResponseError, match="no choices"):
            make_client(handler).chat([{"role": "user", "content": "x"}])


class TestStaleAutoPickedModel:
    """After auto-picking, a model swap in LM Studio makes the cached id 404
    on every chat call; the client must drop the stale id and re-list once
    instead of silently failing until restart.
    """

    def test_model_swap_relists_and_recovers(self):
        import json

        state = {"loaded": "old-model", "models_calls": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/models"):
                state["models_calls"] += 1
                return httpx.Response(200, json={"data": [{"id": state["loaded"]}]})
            model = json.loads(request.content)["model"]
            if model != state["loaded"]:
                return httpx.Response(404, json={"error": "model not found"})
            return chat_response("ok")

        client = make_client(handler, model="")
        assert client.chat([{"role": "user", "content": "x"}]) == "ok"
        assert client.model == "old-model"

        state["loaded"] = "new-model"  # the user swaps models in LM Studio
        assert client.chat([{"role": "user", "content": "x"}]) == "ok"
        assert client.model == "new-model"
        assert state["models_calls"] == 2

    def test_404_after_relist_still_raises_model_error(self):
        # The re-list happens once per chat call, never in a loop: if the
        # freshly listed model still 404s, the caller gets the model error.
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": "ghost-model"}]})
            return httpx.Response(404, json={"error": "model not found"})

        client = make_client(handler, model="")
        with pytest.raises(LMStudioModelError, match="ghost-model"):
            client.chat([{"role": "user", "content": "x"}])

    def test_explicitly_configured_model_does_not_relist_on_404(self):
        # A user-pinned model id is respected: a 404 raises with the actionable
        # hint rather than silently substituting a different model.
        models_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/models"):
                models_calls.append(1)
                return httpx.Response(200, json={"data": [{"id": "other-model"}]})
            return httpx.Response(404, json={"error": "model not found"})

        with pytest.raises(LMStudioModelError, match="pinned-model"):
            make_client(handler, model="pinned-model").chat(
                [{"role": "user", "content": "x"}]
            )
        assert models_calls == []


class TestLocalFirstGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "https://api.openai.com/v1",
            "https://api.anthropic.com/v1",
            "https://api.wisprflow.ai/v1",
        ],
    )
    def test_cloud_ai_endpoints_are_refused(self, url):
        with pytest.raises(ConfigError, match="local-first"):
            LMStudioClient(url)

    def test_localhost_is_accepted(self):
        LMStudioClient("http://localhost:1234/v1")

    def test_lan_host_is_accepted_with_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            LMStudioClient("http://192.168.1.50:1234/v1")
        assert any("not localhost" in r.message for r in caplog.records)
