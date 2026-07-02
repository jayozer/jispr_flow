"""Exception types with actionable, user-facing messages.

Every error raised at an adapter boundary carries a ``hint`` explaining how
the user can fix the problem (grant a permission, start a server, install an
extra), so the CLI can print something more useful than a traceback.
"""

from __future__ import annotations


class LocalFlowError(Exception):
    """Base class for all local-flow errors."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        self.message = message
        self.hint = hint
        super().__init__(f"{message}\nHint: {hint}" if hint else message)


class ConfigError(LocalFlowError):
    """Invalid or unsafe configuration (bad value, cloud AI endpoint, ...)."""


class AudioBackendMissingError(LocalFlowError):
    """The audio capture backend (sounddevice) is not installed."""


class MicPermissionError(LocalFlowError):
    """The OS denied microphone access or no usable input device exists."""


class MicNotFoundError(LocalFlowError):
    """No input audio device could be found."""


class VADBackendMissingError(LocalFlowError):
    """The requested VAD backend is not installed."""


class ASRBackendMissingError(LocalFlowError):
    """The requested ASR backend (e.g. faster-whisper) is not installed."""


class ASRModelMissingError(LocalFlowError):
    """The ASR model could not be found or loaded."""


class LMStudioError(LocalFlowError):
    """Base class for LM Studio client errors."""


class LMStudioConnectionError(LMStudioError):
    """The LM Studio server is unreachable (not running, wrong URL, timeout)."""


class LMStudioModelError(LMStudioError):
    """No model is loaded in LM Studio, or the configured model is unknown."""


class LMStudioResponseError(LMStudioError):
    """LM Studio answered, but the response was an error or malformed."""


class ClipboardError(LocalFlowError):
    """Copying to the system clipboard failed."""


class PasteError(LocalFlowError):
    """Inserting text into the active application failed."""


class HotkeyBackendMissingError(LocalFlowError):
    """The global hotkey backend (pynput) is not installed or unusable."""
