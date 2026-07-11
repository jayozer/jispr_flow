"""Headless controller for the native Settings window."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from local_flow.config import ConfigSnapshot
from local_flow.errors import LocalFlowError
from local_flow.llm.lmstudio import LMStudioClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.settings.service import SettingsService

PARAKEET_V3 = "mlx-community/parakeet-tdt-0.6b-v3"
WHISPER_TURBO = "mlx-community/whisper-large-v3-turbo"

ASR_PRESETS: dict[str, dict[str, object]] = {
    "Whisper Turbo": {
        "asr_backend": "mlx-whisper",
        "asr_model": WHISPER_TURBO,
        "asr_language": "en",
    },
    "Parakeet v3": {
        "asr_backend": "mlx-parakeet",
        "asr_model": PARAKEET_V3,
        "asr_language": "auto",
    },
    "Custom": {},
}


@dataclass(frozen=True)
class SettingsViewModel:
    snapshot: ConfigSnapshot
    styles: list[str]
    dictionary_entries: list[dict]
    aliases: dict[str, str]


class SettingsController:
    def __init__(
        self,
        service: SettingsService | None = None,
        *,
        client_factory: Callable[..., LMStudioClient] = LMStudioClient,
    ) -> None:
        self.service = service or SettingsService()
        self.client_factory = client_factory

    def load(self) -> SettingsViewModel:
        snapshot = self.service.load()
        store = PersonalizationStore(snapshot.config.data_dir)
        return SettingsViewModel(
            snapshot=snapshot,
            styles=sorted(store.styles()),
            dictionary_entries=store.dictionary_entries(),
            aliases=store.snippets(),
        )

    @staticmethod
    def preset(name: str) -> dict[str, object]:
        return dict(ASR_PRESETS.get(name, {}))

    @staticmethod
    def matching_preset(config) -> str:
        for name, values in ASR_PRESETS.items():
            if name == "Custom":
                continue
            if all(getattr(config, field) == value for field, value in values.items()):
                return name
        return "Custom"

    def save(self, changes: Mapping[str, object]) -> SettingsViewModel:
        self.service.save(changes)
        return self.load()

    def refresh_models(self) -> tuple[list[str], str]:
        config = self.service.load().config
        try:
            client = self.client_factory(
                base_url=config.lmstudio_base_url,
                model=config.lmstudio_model,
                timeout=min(config.lmstudio_timeout, 5.0),
            )
            try:
                models = client.list_models()
            finally:
                client.close()
        except LocalFlowError as exc:
            return [], exc.message
        if not models:
            return [], "LM Studio is reachable, but no model is loaded."
        return models, f"LM Studio ready: {len(models)} model(s)."

    def add_dictionary(self, term: str) -> bool:
        return self._store().add_dictionary_term(term)

    def update_dictionary(self, original: str, term: str, *, starred: bool = False) -> bool:
        return self._store().update_dictionary_term(original, term, starred=starred)

    def remove_dictionary(self, term: str) -> bool:
        return self._store().remove_dictionary_term(term)

    def set_alias(self, trigger: str, expansion: str) -> None:
        self._store().set_snippet(trigger, expansion)

    def update_alias(self, original: str, trigger: str, expansion: str) -> bool:
        return self._store().update_snippet(original, trigger, expansion)

    def remove_alias(self, trigger: str) -> bool:
        return self._store().remove_snippet(trigger)

    def _store(self) -> PersonalizationStore:
        return PersonalizationStore(self.service.load().config.data_dir)
