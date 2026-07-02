"""Global hotkey interface and the pynput implementation.

Platform caveats (see README): macOS requires Accessibility + Input
Monitoring permissions; Wayland sessions usually cannot observe global
hotkeys at all, so run hands-free mode there instead.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from local_flow.errors import HotkeyBackendMissingError


class HotkeyListener(ABC):
    """Watches one push-to-talk key and reports press/release."""

    @abstractmethod
    def run(self, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        """Block, invoking callbacks when the hotkey is held/released."""


class PynputPushToTalk(HotkeyListener):
    def __init__(self, key_name: str = "f9") -> None:
        try:
            from pynput import keyboard
        except ImportError as exc:
            raise HotkeyBackendMissingError(
                "The 'pynput' package is not installed.",
                hint="Install desktop extras: uv sync --extra desktop.",
            ) from exc
        self._keyboard = keyboard
        self.key_name = key_name
        self._target = self._resolve_key(key_name)

    def _resolve_key(self, key_name: str):
        keyboard = self._keyboard
        special = getattr(keyboard.Key, key_name.lower(), None)
        if special is not None:
            return special
        if len(key_name) == 1:
            return keyboard.KeyCode.from_char(key_name)
        raise HotkeyBackendMissingError(
            f"Unknown hotkey {key_name!r}.",
            hint="Use a pynput key name such as f9, f8, scroll_lock, or a "
            "single character.",
        )

    def run(self, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        keyboard = self._keyboard
        held = False

        def handle_press(key) -> None:
            nonlocal held
            if key == self._target and not held:
                held = True
                on_press()

        def handle_release(key) -> None:
            nonlocal held
            if key == self._target and held:
                held = False
                on_release()

        try:
            with keyboard.Listener(
                on_press=handle_press, on_release=handle_release
            ) as listener:
                listener.join()
        except Exception as exc:
            raise HotkeyBackendMissingError(
                f"The global hotkey listener failed: {exc}",
                hint="macOS: grant Accessibility AND Input Monitoring permission "
                "to your terminal. Linux/Wayland: global key capture is blocked "
                "by the compositor - use hands-free mode "
                "(LOCAL_FLOW_MODE=hands-free) instead.",
            ) from exc
