"""Configuration boundary for native guided setup; browsing never starts ASR."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from local_flow.errors import ConfigError
from local_flow.settings.controller import SettingsController
from local_flow.settings.service import is_environment_owned, is_settings_editable


class DownloadManager(Protocol):
    def inventory(self) -> list[dict]: ...
    def start(self, model_id: str) -> dict: ...
    def cancel(self, job_id: str) -> dict: ...
    def snapshot(self) -> dict | None: ...
    def local_model(self, model_id: str) -> tuple[str, str | Path]: ...
    def close(self) -> None: ...


def _create_manager(data_dir: Path, *, on_progress: Callable[[dict], None]) -> DownloadManager:
    from local_flow.models import ModelDownloadManager

    return ModelDownloadManager(data_dir, on_progress=on_progress)


_SETUP_FIELDS = ("asr_profile", "asr_backend", "asr_model", "asr_language", "polish_backend")


class GuidedSetup:
    """Keep downloads separate from an explicit, atomic model configuration change."""

    def __init__(self, controller, on_progress, *, manager_factory=None):
        self.controller: SettingsController = controller
        self.on_progress = on_progress
        self.manager_factory = manager_factory or _create_manager
        self._manager: DownloadManager | None = None
        self._data_dir: Path | None = None
        self._generation = 0

    def manager(self) -> DownloadManager:
        data_dir = self.controller.service.load().config.data_dir
        if self._manager is None or data_dir != self._data_dir:
            self.close()
            self._data_dir = data_dir
            generation = self._generation

            def publish(progress):
                if generation == self._generation:
                    self.on_progress(progress)

            self._manager = self.manager_factory(data_dir, on_progress=publish)
        return self._manager

    def catalog(self) -> dict:
        snapshot = self.controller.service.load()
        config = snapshot.config
        manager = self.manager()
        models = manager.inventory()
        selected = next((model["id"] for model in models
                         if model["backend"] == config.asr_backend
                         and config.asr_model in (model["repo_id"], model.get("local_path"))), None)
        locked = [name for name in _SETUP_FIELDS
                  if not is_settings_editable(snapshot.sources[name])]
        has_polish_preference = snapshot.config_path is not None or any(
            snapshot.sources[name] != "default"
            for name in ("polish_backend", "lmstudio_model", "lmstudio_base_url")
        )
        return {
            "models": models,
            "current_model_id": selected,
            "current_language": config.asr_language,
            "polish_backend": config.polish_backend if has_polish_preference else "rules",
            "can_apply": not locked,
            "configuration_notice": (
                "These settings are controlled by environment overrides: " + ", ".join(locked)
                if locked else None
            ),
            "download": manager.snapshot(),
        }

    def prepare_apply(self, model_id: str, language: str, polish_backend: str) -> dict:
        manager = self.manager()
        model = next((item for item in manager.inventory() if item["id"] == model_id), None)
        if model is None:
            raise ConfigError("Choose one of the available speech models.")
        if not model["available"]:
            raise ConfigError(model.get("unavailable_reason") or "This model is unavailable.")
        if language not in model["languages"]:
            raise ConfigError("Choose a language supported by the selected speech model.")
        if polish_backend not in ("rules", "lmstudio"):
            raise ConfigError("Choose local rules or your configured LM Studio for writing polish.")
        backend, path = manager.local_model(model_id)
        changes = {
            "asr_profile": "custom", "asr_backend": backend, "asr_model": str(path),
            "asr_language": language, "polish_backend": polish_backend,
        }
        if backend == "faster-whisper":
            changes.update(asr_device="cpu", asr_compute_type="int8")
        snapshot = self.controller.service.load()
        for name, value in changes.items():
            if (value != getattr(snapshot.config, name)
                    and is_environment_owned(snapshot.sources[name])):
                raise ConfigError(
                    f"{name} is controlled by {snapshot.sources[name]}.",
                    hint="Remove that override before applying guided setup.",
                )
        return changes

    def validate_current_language(self, language: str) -> None:
        """Managed/cache paths must retain the catalog's language constraints."""
        config = self.controller.service.load().config
        for model in self.manager().inventory():
            if (model["backend"] == config.asr_backend
                    and config.asr_model in (model["repo_id"], model.get("local_path"))
                    and language not in model["languages"]):
                raise ConfigError(
                    f"{model['name']} does not support that language.",
                    hint="Choose a supported language or change the speech model in Guided Setup.",
                )

    def close(self) -> None:
        self._generation += 1
        manager, self._manager = self._manager, None
        if manager is not None:
            manager.close()
