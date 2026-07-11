"""Headless coverage for the native macOS app's JSONL bridge."""

from __future__ import annotations

import io
import json

from local_flow.app_host import AppHost, JSONLReporter, JSONLWriter
from local_flow.settings.controller import SettingsController
from local_flow.settings.service import SettingsService


class FakeSession:
    def __init__(self, config, reporter) -> None:
        self.config = config
        self.reporter = reporter
        self.running = False
        self.styles: list[str] = []
        self.languages: list[str] = []
        self.stop_calls = 0

    def start(self) -> None:
        self.running = True
        self.reporter.notify("idle")

    def stop(self) -> bool:
        self.stop_calls += 1
        self.running = False
        return True

    def set_style(self, name: str) -> None:
        self.styles.append(name)

    def set_language(self, code: str) -> None:
        self.languages.append(code)


def _controller(tmp_path, *, env=None) -> SettingsController:
    path = tmp_path / "config.toml"
    path.write_text(
        f'data_dir = "{tmp_path / "data"}"\n'
        'asr_profile = "custom"\n'
        'asr_backend = "mlx-parakeet"\n'
        'asr_model = "mlx-community/parakeet-tdt-0.6b-v3"\n'
        'asr_language = "auto"\n'
    )
    effective_env = {"LOCAL_FLOW_CONFIG": str(path), **(env or {})}
    return SettingsController(service=SettingsService(env=effective_env))


def _messages(output: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in output.getvalue().splitlines()]


def _request(request_id: str, command: str, payload=None) -> str:
    return json.dumps(
        {"v": 1, "id": request_id, "command": command, "payload": payload or {}}
    )


def test_host_emits_ready_snapshot_and_lifecycle_replies(tmp_path):
    output = io.StringIO()
    created: list[FakeSession] = []

    def factory(config, reporter):
        session = FakeSession(config, reporter)
        created.append(session)
        return session

    input_stream = io.StringIO(
        "\n".join(
            [
                _request("start-1", "start"),
                _request("reload-1", "reload", {"start": True}),
                _request("quit-1", "shutdown"),
            ]
        )
        + "\n"
    )

    code = AppHost(
        input_stream=input_stream,
        output_stream=output,
        controller=_controller(tmp_path),
        session_factory=factory,
    ).run()

    messages = _messages(output)
    assert code == 0
    assert messages[0] == {"v": 1, "event": "ready", "protocol": 1}
    snapshot = next(message["snapshot"] for message in messages if message["event"] == "snapshot")
    assert snapshot["settings"]["mode"]["editable"] is True
    assert snapshot["settings"]["asr_backend"]["value"] == "mlx-parakeet"
    assert snapshot["styles"]
    assert snapshot["transforms"]
    replies = {message["id"]: message for message in messages if message["event"] == "reply"}
    assert replies["start-1"]["result"]["running"] is True
    assert replies["reload-1"]["result"]["running"] is True
    assert replies["quit-1"]["result"]["stopped"] is True
    assert len(created) == 2


def test_save_live_choices_and_personalization_crud(tmp_path):
    output = io.StringIO()
    sessions: list[FakeSession] = []

    def factory(config, reporter):
        session = FakeSession(config, reporter)
        sessions.append(session)
        return session

    commands = [
        _request("start", "start"),
        _request("save", "save_settings", {"changes": {"cleanup_level": "light"}}),
        _request("style", "set_style", {"name": "casual"}),
        _request("language", "set_language", {"code": "fr"}),
        _request("dict-add", "dictionary_add", {"term": "JiSpr"}),
        _request(
            "dict-update",
            "dictionary_update",
            {"original": "JiSpr", "term": "JiSpr Flow", "starred": True},
        ),
        _request("alias-add", "alias_add", {"trigger": "jisper", "expansion": "JiSpr"}),
        _request("alias-remove", "alias_remove", {"trigger": "jisper"}),
        _request("dict-remove", "dictionary_remove", {"term": "JiSpr Flow"}),
        _request("quit", "shutdown"),
    ]

    AppHost(
        input_stream=io.StringIO("\n".join(commands) + "\n"),
        output_stream=output,
        controller=_controller(tmp_path),
        session_factory=factory,
    ).run()

    messages = _messages(output)
    assert not [message for message in messages if message["event"] == "error"]
    assert sessions[0].styles == ["casual"]
    assert sessions[0].languages == ["fr"]
    final_snapshot = [
        message["snapshot"] for message in messages if message["event"] == "snapshot"
    ][-1]
    assert final_snapshot["settings"]["cleanup_level"]["value"] == "light"
    assert final_snapshot["settings"]["style"]["value"] == "casual"
    assert final_snapshot["settings"]["asr_language"]["value"] == "fr"
    assert final_snapshot["dictionary"] == []
    assert final_snapshot["aliases"] == {}


def test_malformed_messages_and_locked_fields_return_structured_errors(tmp_path):
    output = io.StringIO()
    controller = _controller(tmp_path, env={"LOCAL_FLOW_PILL_STYLE": "expanded"})
    input_stream = io.StringIO(
        "not-json\n"
        + _request("locked", "save_settings", {"changes": {"pill_style": "compact"}})
        + "\n"
        + _request("quit", "shutdown")
        + "\n"
    )

    AppHost(
        input_stream=input_stream,
        output_stream=output,
        controller=controller,
        session_factory=FakeSession,
    ).run()

    messages = _messages(output)
    snapshot = next(message["snapshot"] for message in messages if message["event"] == "snapshot")
    assert snapshot["settings"]["pill_style"] == {
        "value": "expanded",
        "source": "environment",
        "editable": False,
    }
    errors = [message for message in messages if message["event"] == "error"]
    assert len(errors) == 2
    assert errors[0]["id"] is None
    assert "Expecting value" in errors[0]["message"]
    assert errors[1]["id"] == "locked"
    assert "overridden by environment" in errors[1]["message"]


def test_reporter_emits_state_and_clamped_audio_level():
    output = io.StringIO()
    reporter = JSONLReporter(JSONLWriter(output))

    reporter.notify("recording", "Listening")
    reporter.audio_level(1.5)

    assert _messages(output) == [
        {"v": 1, "event": "state", "state": "recording", "detail": "Listening"},
        {"v": 1, "event": "audio_level", "level": 1.0},
    ]
