"""Versioned JSONL bridge used by the native JiSpr macOS app.

The bridge deliberately keeps Python as the authority for configuration,
personalization, and the dictation runtime.  Swift owns presentation and sends
small commands over stdin; this process emits replies and state events on
stdout.  Human-readable diagnostics remain on stderr.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import asdict, fields
from pathlib import Path
from typing import IO, TYPE_CHECKING, Protocol

from local_flow.config import Config
from local_flow.errors import ConfigError, LocalFlowError
from local_flow.personalization.corrections import (
    CorrectionChange,
    CorrectionStore,
    suggest_corrections,
)
from local_flow.personalization.review import MAX_REVIEW_CHARS, DictationReviews
from local_flow.settings.controller import ASR_PRESETS, SettingsController
from local_flow.settings.service import SETTINGS_FIELDS, is_settings_editable
from local_flow.settings.setup import GuidedSetup
from local_flow.status import State, StatusReporter

PROTOCOL_VERSION = 1
_JOIN_TIMEOUT = 3.0

if TYPE_CHECKING:
    from local_flow.pipeline import DictationResult

SETTING_OPTIONS: dict[str, list[str]] = {
    "mode": ["push-to-talk", "hands-free"],
    "mouse_button": ["", "middle", "x1", "x2"],
    "mouse_mode": ["hold", "toggle"],
    "mouse_enter_button": ["", "middle", "x1", "x2"],
    "asr_profile": ["accuracy", "fast", "custom"],
    "asr_backend": ["mlx-parakeet", "mlx-whisper", "faster-whisper"],
    "asr_device": ["auto", "cpu", "cuda"],
    "asr_compute_type": ["int8", "float16", "float32"],
    "polish_backend": ["lmstudio", "rules"],
    "cleanup_level": ["none", "light", "medium", "high"],
    "insert_method": ["auto", "paste", "type", "clipboard"],
    "streaming": ["off", "sentence", "live-preview"],
    "pill_style": ["compact", "expanded"],
    "pill_position": ["bottom", "caret"],
    "vad_backend": ["energy", "webrtc"],
    "vad_preset": ["normal", "whisper"],
    "history_retention": ["forever", "24h", "off"],
}


def _json_value(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


class JSONLWriter:
    """Serialize protocol messages without interleaving background events."""

    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, event: str, **payload: object) -> None:
        message = {"v": PROTOCOL_VERSION, "event": event, **payload}
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._stream.write(encoded + "\n")
            self._stream.flush()


class JSONLReporter(StatusReporter):
    """Convert runtime status and microphone levels into protocol events."""

    wants_audio_level = True

    def __init__(
        self,
        writer: JSONLWriter,
        *,
        state_callback: Callable[[State], None] | None = None,
        dictation_callback: Callable[[DictationResult], None] | None = None,
    ) -> None:
        self._writer = writer
        self._state_callback = state_callback
        self._dictation_callback = dictation_callback

    def dictation_completed(self, result: DictationResult) -> None:
        if self._dictation_callback is not None:
            self._dictation_callback(result)

    def notify(self, state: State, detail: str = "") -> None:
        if self._state_callback is not None:
            self._state_callback(state)
        self._writer.send("state", state=state, detail=detail)

    def audio_level(self, level: float) -> None:
        self._writer.send("audio_level", level=max(0.0, min(float(level), 1.0)))


class Session(Protocol):
    @property
    def running(self) -> bool: ...

    def start(self) -> None: ...

    def stop(self) -> bool: ...

    def set_style(self, name: str) -> None: ...

    def set_language(self, code: str) -> None: ...


class RuntimeSession:
    """Own the existing run-loop dependencies on behalf of the native app."""

    def __init__(self, config: Config, reporter: StatusReporter) -> None:
        # Import lazily so importing/testing the bridge never imports optional
        # microphone, ASR, hotkey, or platform packages.
        from local_flow.app import _build_run_dependencies

        self.config = config
        self.reporter = reporter
        self._deps = _build_run_dependencies(config)
        self._deps.pipeline.on_dictation = getattr(reporter, "dictation_completed", None)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        from local_flow.app import _run_loop

        self._stop_event = threading.Event()

        def run() -> None:
            try:
                _run_loop(
                    self.config,
                    self.config.mode,
                    self.reporter,
                    self._stop_event,
                    self._deps,
                    quiet=True,
                )
            except Exception as exc:
                self.reporter.notify("error", f"Dictation engine stopped: {exc}")

        self._thread = threading.Thread(target=run, name="jispr-engine", daemon=True)
        self._thread.start()

    def stop(self) -> bool:
        if self._thread is None:
            return True
        self._stop_event.set()
        self._thread.join(timeout=_JOIN_TIMEOUT)
        stopped = not self._thread.is_alive()
        if stopped:
            self.reporter.notify("idle")
        return stopped

    def set_style(self, name: str) -> None:
        self._deps.pipeline.polisher.style = name

    def set_language(self, code: str) -> None:
        self._deps.pipeline.transcriber.language = code


SessionFactory = Callable[[Config, StatusReporter], Session]


class AppHost:
    """Command dispatcher for one native-app bridge process."""

    def __init__(
        self,
        *,
        input_stream: IO[str],
        output_stream: IO[str],
        controller: SettingsController | None = None,
        session_factory: SessionFactory = RuntimeSession,
        model_manager_factory=None,
    ) -> None:
        self.input_stream = input_stream
        self.writer = JSONLWriter(output_stream)
        self.controller = controller or SettingsController()
        self.session_factory = session_factory
        self.session: Session | None = None
        self.state: State = "idle"
        self.setup = GuidedSetup(
            self.controller, self._publish_download, manager_factory=model_manager_factory
        )
        self.reviews = DictationReviews()
        self._undo_changes: OrderedDict[str, tuple[Path, CorrectionChange]] = OrderedDict()
        self.reporter = JSONLReporter(
            self.writer,
            state_callback=self._set_state,
            dictation_callback=self._publish_dictation,
        )

    def _publish_dictation(self, result: DictationResult) -> None:
        review = self.reviews.add(result)
        self.writer.send("dictation", dictation=asdict(review) if review is not None else None)

    def _publish_download(self, progress: dict) -> None:
        try:
            self.writer.send("model_download", download=progress)
        except (BrokenPipeError, OSError):
            # The app may have closed while the download worker was unwinding.
            pass

    def _corrections(self) -> CorrectionStore:
        return CorrectionStore(self.controller.service.load().config.data_dir)

    def _remember_change(
        self, store: CorrectionStore, change: CorrectionChange | None
    ) -> str | None:
        if change is None:
            return None
        # Undo is explicit and session-local. An older token must not undo a
        # later decision, including an edit made through a second review.
        self._undo_changes.clear()
        self._undo_changes[change.token] = (store.path.parent, change)
        return change.token

    def _review_correction(self, payload: Mapping[str, object]):
        review_id = self._required_string(payload, "dictation_id")
        review = self.reviews.get(review_id)
        corrected = payload.get("corrected_text")
        if not isinstance(corrected, str):
            raise ConfigError("Corrected text must be a string.")
        if len(corrected) > MAX_REVIEW_CHARS:
            raise ConfigError("Corrections can review up to 32,000 characters at once.")
        return review, corrected, suggest_corrections(review.text, corrected)

    def _set_state(self, state: State) -> None:
        self.state = state

    def snapshot_payload(self) -> dict[str, object]:
        view_model = self.controller.load()
        config = view_model.snapshot.config
        corrections_error = None
        try:
            corrections = [asdict(rule) for rule in CorrectionStore(config.data_dir).list_rules()]
        except (ConfigError, OSError) as exc:
            corrections, corrections_error = [], str(exc)
        settings: dict[str, object] = {}
        for field in fields(config):
            name = field.name
            source = view_model.snapshot.sources[name]
            settings[name] = {
                "value": _json_value(getattr(config, name)),
                "source": source,
                "editable": name in SETTINGS_FIELDS and is_settings_editable(source),
            }
        return {
            "config_path": (
                str(view_model.snapshot.config_path)
                if view_model.snapshot.config_path is not None
                else None
            ),
            "data_dir": str(config.data_dir),
            "settings": settings,
            "options": SETTING_OPTIONS,
            "presets": ASR_PRESETS,
            "styles": view_model.styles,
            "transforms": view_model.transforms,
            "dictionary": view_model.dictionary_entries,
            "aliases": view_model.aliases,
            "corrections": corrections,
            "corrections_error": corrections_error,
        }

    def emit_snapshot(self) -> None:
        self.writer.send("snapshot", snapshot=self.snapshot_payload())

    def _reply(self, request_id: str, result: object | None = None) -> None:
        self.writer.send("reply", id=request_id, ok=True, result=result or {})

    def _error(self, request_id: object, exc: BaseException | str) -> None:
        if isinstance(exc, LocalFlowError):
            message = exc.message
            hint = exc.hint
        else:
            message = str(exc)
            hint = None
        payload: dict[str, object] = {"id": request_id, "message": message}
        if hint:
            payload["hint"] = hint
        self.writer.send("error", **payload)

    def _ensure_session(self) -> Session:
        if self.session is None:
            config = self.controller.load().snapshot.config
            self.session = self.session_factory(config, self.reporter)
        return self.session

    @staticmethod
    def _payload(message: Mapping[str, object]) -> dict[str, object]:
        payload = message.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        return payload

    @staticmethod
    def _required_string(payload: Mapping[str, object], name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"payload.{name} must be a non-empty string")
        return value

    def _reload_session(self, *, start: bool) -> dict[str, object]:
        stopped = True
        if self.session is not None:
            stopped = self.session.stop()
            if not stopped:
                return {"running": self.session.running, "stopped": False}
        self.session = None
        if start:
            self._ensure_session().start()
        return {"running": bool(self.session and self.session.running), "stopped": stopped}

    def handle(self, message: Mapping[str, object]) -> bool:
        request_id = message.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("id must be a non-empty string")
        if message.get("v") != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version; expected {PROTOCOL_VERSION}")
        command = message.get("command")
        if not isinstance(command, str):
            raise ValueError("command must be a string")
        payload = self._payload(message)

        if command == "setup_catalog":
            self._reply(request_id, self.setup.catalog())
        elif command == "model_download":
            progress = self.setup.manager().start(self._required_string(payload, "model_id"))
            self._reply(request_id, {"download": progress})
        elif command == "model_cancel":
            progress = self.setup.manager().cancel(self._required_string(payload, "job_id"))
            self._reply(request_id, {"download": progress})
        elif command == "setup_apply":
            if self.state in ("recording", "processing", "preview"):
                raise ConfigError("Finish or cancel the current dictation before changing models.")
            changes = self.setup.prepare_apply(
                self._required_string(payload, "model_id"),
                self._required_string(payload, "language"),
                self._required_string(payload, "polish_backend"),
            )
            if self.session is not None and not self.session.stop():
                raise ConfigError("The engine did not stop. Model settings were left unchanged.")
            self.session = None
            self.writer.send("runtime", running=False)
            self.controller.save(changes)
            self.reporter.notify(
                "idle", "Speech model saved. Start the practice dictation when ready."
            )
            self.emit_snapshot()
            self._reply(request_id, {"applied": True})
        elif command == "review_latest":
            review = self.reviews.latest()
            self._reply(request_id, {"dictation": asdict(review) if review is not None else None})
        elif command == "correction_preview":
            review, _, suggestions = self._review_correction(payload)
            self._reply(request_id, {
                "dictation_id": review.id,
                "suggestions": [asdict(suggestion) for suggestion in suggestions],
            })
        elif command == "correction_learn":
            review, _, suggestions = self._review_correction(payload)
            source = self._required_string(payload, "source")
            replacement = self._required_string(payload, "replacement")
            if not any(
                suggestion.source == source and suggestion.replacement == replacement
                for suggestion in suggestions
            ):
                raise ConfigError("Review this text change before remembering it.")
            scope = payload.get("scope")
            if scope not in ("app", "global"):
                raise ConfigError("Choose this app or everywhere for the correction.")
            if scope == "app" and not review.app_id:
                raise ConfigError("This dictation has no app identity; choose Everywhere.")
            store = self._corrections()
            change = store.learn(
                source, replacement, app_id=review.app_id if scope == "app" else ""
            )
            token = self._remember_change(store, change)
            self.emit_snapshot()
            self._reply(request_id, {
                "changed": change is not None,
                "undo_token": token,
                "rule": asdict(change.after) if change and change.after else None,
            })
        elif command == "correction_forget":
            store = self._corrections()
            change = store.forget(self._required_string(payload, "rule_id"))
            token = self._remember_change(store, change)
            self.emit_snapshot()
            self._reply(request_id, {"changed": change is not None, "undo_token": token})
        elif command == "correction_undo":
            token = self._required_string(payload, "token")
            saved = self._undo_changes.get(token)
            if saved is None:
                raise ConfigError("This Undo is no longer available.")
            data_dir, change = saved
            changed = CorrectionStore(data_dir).undo(change)
            if not changed:
                raise ConfigError("The correction has changed; review it before undoing.")
            self._undo_changes.pop(token, None)
            self.emit_snapshot()
            self._reply(request_id, {"changed": True})
        elif command == "start":
            session = self._ensure_session()
            session.start()
            self._reply(request_id, {"running": session.running})
        elif command == "stop":
            stopped = True if self.session is None else self.session.stop()
            running = bool(self.session and self.session.running)
            self._reply(request_id, {"running": running, "stopped": stopped})
        elif command == "reload":
            should_start = bool(payload.get("start", self.session is not None))
            result = self._reload_session(start=should_start)
            self.emit_snapshot()
            self._reply(request_id, result)
        elif command == "save_settings":
            changes = payload.get("changes")
            if not isinstance(changes, dict):
                raise ValueError("payload.changes must be a JSON object")
            self.controller.save(changes)
            self.emit_snapshot()
            self._reply(request_id, {"requires_restart": bool(changes)})
        elif command == "refresh_models":
            models, status = self.controller.refresh_models()
            self._reply(request_id, {"models": models, "status": status})
        elif command == "set_style":
            name = self._required_string(payload, "name")
            if name not in self.controller.load().styles:
                raise ValueError(f"unknown writing style: {name}")
            self.controller.save({"style": name})
            if self.session is not None:
                self.session.set_style(name)
            self.emit_snapshot()
            self._reply(request_id)
        elif command == "set_language":
            code = self._required_string(payload, "code")
            self.setup.validate_current_language(code)
            self.controller.save({"asr_language": code})
            if self.session is not None:
                self.session.set_language(code)
            self.emit_snapshot()
            self._reply(request_id)
        elif command == "dictionary_add":
            changed = self.controller.add_dictionary(self._required_string(payload, "term"))
            self.emit_snapshot()
            self._reply(request_id, {"changed": changed})
        elif command == "dictionary_update":
            changed = self.controller.update_dictionary(
                self._required_string(payload, "original"),
                self._required_string(payload, "term"),
                starred=bool(payload.get("starred", False)),
            )
            self.emit_snapshot()
            self._reply(request_id, {"changed": changed})
        elif command == "dictionary_remove":
            changed = self.controller.remove_dictionary(
                self._required_string(payload, "term")
            )
            self.emit_snapshot()
            self._reply(request_id, {"changed": changed})
        elif command == "alias_add":
            self.controller.set_alias(
                self._required_string(payload, "trigger"),
                str(payload.get("expansion", "")),
            )
            self.emit_snapshot()
            self._reply(request_id)
        elif command == "alias_update":
            changed = self.controller.update_alias(
                self._required_string(payload, "original"),
                self._required_string(payload, "trigger"),
                str(payload.get("expansion", "")),
            )
            self.emit_snapshot()
            self._reply(request_id, {"changed": changed})
        elif command == "alias_remove":
            changed = self.controller.remove_alias(
                self._required_string(payload, "trigger")
            )
            self.emit_snapshot()
            self._reply(request_id, {"changed": changed})
        elif command == "shutdown":
            self.setup.close()
            stopped = True if self.session is None else self.session.stop()
            self._reply(request_id, {"stopped": stopped})
            return False
        else:
            raise ValueError(f"unknown command: {command}")
        return True

    def run(self) -> int:
        try:
            return self._run_commands()
        finally:
            self.setup.close()

    def _run_commands(self) -> int:
        self.writer.send("ready", protocol=PROTOCOL_VERSION)
        try:
            self.emit_snapshot()
        except Exception as exc:
            self._error(None, exc)

        for raw_line in self.input_stream:
            line = raw_line.strip()
            if not line:
                continue
            request_id: object = None
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("message must be a JSON object")
                request_id = message.get("id")
                if not self.handle(message):
                    return 0
            except Exception as exc:
                self._error(request_id, exc)
        if self.session is not None:
            self.session.stop()
        return 0


def run_app_host(input_stream: IO[str], output_stream: IO[str]) -> int:
    from local_flow.runtime_lock import RuntimeInstanceLock

    host = AppHost(input_stream=input_stream, output_stream=output_stream)
    config = host.controller.load().snapshot.config
    with RuntimeInstanceLock(config.data_dir, "native app-host"):
        return host.run()
