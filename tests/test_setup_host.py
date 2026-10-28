"""Guided setup stays offline until Download and applies model settings explicitly."""

import io
import json
import tomllib

import pytest

from local_flow.app_host import AppHost
from local_flow.errors import ConfigError
from local_flow.settings.controller import SettingsController
from local_flow.settings.service import SettingsService
from local_flow.settings.setup import GuidedSetup


class FakeDownloads:
    def __init__(self, data_dir, *, on_progress):
        self.data_dir = data_dir
        self.on_progress = on_progress
        self.closed = False
        self.job = None
        self.models = [{
            "id": "whisper-test", "name": "Test speech model", "description": "A fixture",
            "backend": "faster-whisper", "repo_id": "fixture/model", "languages": ["auto", "en"],
            "size_hint": "Tiny fixture", "available": True, "unavailable_reason": None,
            "installed": True, "verified": True, "local_path": str(data_dir / "models" / "test"),
        }]

    def inventory(self):
        return self.models

    def start(self, model_id):
        if model_id != "whisper-test":
            raise ConfigError("Unknown model")
        self.job = {
            "job_id": "job-1", "model_id": model_id, "state": "preparing",
            "downloaded_bytes": 0, "total_bytes": None,
            "file_name": None, "error": None, "local_path": None,
        }
        self.on_progress(self.job)
        return self.job

    def cancel(self, job_id):
        if self.job is None or self.job["job_id"] != job_id:
            raise ConfigError("Unknown download")
        self.job = {**self.job, "state": "cancelled"}
        self.on_progress(self.job)
        return self.job

    def snapshot(self):
        return self.job

    def local_model(self, model_id):
        model = next(item for item in self.models if item["id"] == model_id)
        if not model["verified"]:
            raise ConfigError("Verify this model first")
        return model["backend"], model["local_path"]

    def close(self):
        self.closed = True


class Session:
    running = True

    def __init__(self, *, stops=True):
        self.stops = stops
        self.stop_count = 0

    def stop(self):
        self.stop_count += 1
        return self.stops


def make_host(tmp_path, *, extra_env=None, commands=""):
    path = tmp_path / "config.toml"
    path.write_text(
        f'data_dir = "{tmp_path / "data"}"\npill_position = "caret"\nasr_profile = "fast"\n'
    )
    controller = SettingsController(service=SettingsService(env={
        "LOCAL_FLOW_CONFIG": str(path), **(extra_env or {}),
    }))
    output = io.StringIO()
    managers = []

    def factory(data_dir, *, on_progress):
        manager = FakeDownloads(data_dir, on_progress=on_progress)
        managers.append(manager)
        return manager

    def unexpected_session(*_):
        pytest.fail("Setup must not implicitly start dictation or load an ASR model")

    host = AppHost(input_stream=io.StringIO(commands), output_stream=output,
                   controller=controller, session_factory=unexpected_session,
                   model_manager_factory=factory)
    return host, output, managers, path


def command(host, name, payload=None, request_id="test"):
    return host.handle({"v": 1, "id": request_id, "command": name, "payload": payload or {}})


def messages(output):
    return [json.loads(line) for line in output.getvalue().splitlines()]


def apply_payload(**overrides):
    return {"model_id": "whisper-test", "language": "en", "polish_backend": "rules", **overrides}


def test_ready_and_snapshot_do_not_create_download_manager_or_start_session(tmp_path):
    host, output, managers, _ = make_host(tmp_path)
    assert host.run() == 0
    assert managers == []
    assert [item["event"] for item in messages(output)] == ["ready", "snapshot"]


def test_catalog_is_read_only_and_keeps_existing_polish_choice(tmp_path):
    host, output, managers, path = make_host(tmp_path)
    original = path.read_bytes()
    command(host, "setup_catalog")
    result = messages(output)[-1]["result"]
    assert result["models"][0]["verified"] is True
    assert result["polish_backend"] == "lmstudio"
    assert result["can_apply"] is True
    assert path.read_bytes() == original
    assert len(managers) == 1


@pytest.mark.parametrize("existing_polish", [False, True])
def test_first_run_catalog_uses_rules_unless_existing_polish_preference(tmp_path, existing_polish):
    path = tmp_path / "new" / "config.toml"
    env = {"LOCAL_FLOW_DATA_DIR": str(tmp_path / "data")}
    if existing_polish:
        env["LOCAL_FLOW_LMSTUDIO_MODEL"] = "existing-local-model"
    controller = SettingsController(service=SettingsService(env=env, default_path=path))
    setup = GuidedSetup(controller, lambda _: None, manager_factory=FakeDownloads)
    assert setup.catalog()["polish_backend"] == ("lmstudio" if existing_polish else "rules")
    assert not path.exists()
    setup.close()


def test_download_emits_progress_without_changing_settings_and_cancel_is_scoped(tmp_path):
    host, output, _, path = make_host(tmp_path)
    before = path.read_bytes()
    command(host, "model_download", {"model_id": "whisper-test"})
    assert messages(output)[0]["event"] == "model_download"
    assert messages(output)[-1]["result"]["download"]["state"] == "preparing"
    with pytest.raises(ConfigError, match="Unknown download"):
        command(host, "model_cancel", {"job_id": "another-job"})
    command(host, "model_cancel", {"job_id": "job-1"})
    assert messages(output)[-1]["result"]["download"]["state"] == "cancelled"
    assert path.read_bytes() == before


def test_apply_saves_verified_local_model_atomically_and_preserves_other_settings(tmp_path):
    host, output, _, path = make_host(tmp_path)
    old_session = host.session = Session()
    command(host, "setup_apply", apply_payload())
    saved = tomllib.loads(path.read_text())
    effective = host.controller.service.load().config
    assert saved["asr_profile"] == "custom"
    assert saved["asr_backend"] == "faster-whisper"
    assert saved["asr_model"] == str(tmp_path / "data/models/test")
    assert effective.asr_language == "en"
    assert saved["asr_device"] == "cpu"
    assert effective.asr_compute_type == "int8"
    assert saved["polish_backend"] == "rules"
    assert saved["pill_position"] == "caret"
    assert old_session.stop_count == 1 and host.session is None
    assert messages(output)[-1]["result"] == {"applied": True}
    assert [item["event"] for item in messages(output)] == ["runtime", "state", "snapshot", "reply"]


def test_save_failure_after_stopping_reports_paused_and_keeps_old_config(tmp_path, monkeypatch):
    host, output, _, path = make_host(tmp_path)
    host.session = Session()
    before = path.read_bytes()

    def fail(_):
        raise OSError("Disk full")

    monkeypatch.setattr(host.controller, "save", fail)
    with pytest.raises(OSError, match="Disk full"):
        command(host, "setup_apply", apply_payload())
    assert host.session is None
    assert path.read_bytes() == before
    assert messages(output) == [{"v": 1, "event": "runtime", "running": False}]


@pytest.mark.parametrize("state", ["recording", "processing", "preview"])
def test_apply_rejects_in_flight_dictation_without_changing_settings(tmp_path, state):
    host, _, _, path = make_host(tmp_path)
    before = path.read_bytes()
    host.state = state
    with pytest.raises(ConfigError, match="Finish or cancel"):
        command(host, "setup_apply", apply_payload())
    assert path.read_bytes() == before


def test_failed_engine_stop_prevents_config_write(tmp_path):
    host, _, _, path = make_host(tmp_path)
    session = host.session = Session(stops=False)
    before = path.read_bytes()
    with pytest.raises(ConfigError, match="did not stop"):
        command(host, "setup_apply", apply_payload())
    assert path.read_bytes() == before
    assert host.session is session


@pytest.mark.parametrize("override", [
    {"LOCAL_FLOW_ASR_MODEL": "fixed-model"},
    {"LOCAL_FLOW_ASR_PROFILE": "fast"},
    {"LOCAL_FLOW_ASR_BACKEND": "mlx-whisper"},
    {"LOCAL_FLOW_POLISH_BACKEND": "lmstudio"},
])
def test_environment_lock_rejected_before_stopping_or_saving(tmp_path, override):
    host, _, _, path = make_host(tmp_path, extra_env=override)
    session = host.session = Session()
    before = path.read_bytes()
    assert host.setup.catalog()["can_apply"] is False
    with pytest.raises(ConfigError, match="controlled by"):
        command(host, "setup_apply", apply_payload())
    assert path.read_bytes() == before
    assert session.stop_count == 0


@pytest.mark.parametrize("payload", [
    apply_payload(model_id="../../elsewhere"), apply_payload(language="not-a-language"),
    apply_payload(polish_backend="cloud"),
])
def test_invalid_selection_cannot_change_config(tmp_path, payload):
    host, _, _, path = make_host(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ConfigError):
        command(host, "setup_apply", payload)
    assert path.read_bytes() == before


def test_unverified_or_unavailable_model_cannot_be_applied(tmp_path):
    host, _, _, path = make_host(tmp_path)
    model = host.setup.manager().models[0]
    before = path.read_bytes()
    model["verified"] = False
    with pytest.raises(ConfigError, match="Verify"):
        command(host, "setup_apply", apply_payload())
    model["available"] = False
    with pytest.raises(ConfigError, match="unavailable"):
        command(host, "setup_apply", apply_payload())
    assert path.read_bytes() == before


def test_managed_english_model_keeps_language_constraint_after_setup(tmp_path):
    host, _, _, path = make_host(tmp_path)
    host.setup.manager().models[0]["languages"] = ["en"]
    command(host, "setup_apply", apply_payload())
    before = path.read_bytes()
    with pytest.raises(ConfigError, match="does not support"):
        command(host, "set_language", {"code": "auto"})
    assert path.read_bytes() == before


@pytest.mark.parametrize("shutdown", [False, True])
def test_eof_and_shutdown_close_downloads_and_ignore_late_progress(tmp_path, shutdown):
    requests = ["setup_catalog"] + (["shutdown"] if shutdown else [])
    commands = "".join(json.dumps({"v": 1, "id": str(i), "command": name}) + "\n"
                       for i, name in enumerate(requests))
    host, output, managers, _ = make_host(tmp_path, commands=commands)
    assert host.run() == 0
    assert managers[0].closed
    before = output.getvalue()
    managers[0].on_progress({"state": "completed"})
    assert output.getvalue() == before
