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
    """Watches one push-to-talk key and reports press/release/cancel."""

    @abstractmethod
    def run(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        """Block, invoking callbacks when the hotkey is held/released/cancelled."""


class PushToTalkCore:
    """Held-state bookkeeping shared by every push-to-talk listener.

    Translates raw key events into at-most-once press/release/cancel
    callbacks. A cancel while held discards the recording: ``on_cancel``
    fires and the eventual physical key release is swallowed.
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._on_cancel = on_cancel
        self.held = False

    def key_down(self) -> None:
        if not self.held:
            self.held = True
            self._on_press()

    def key_up(self) -> None:
        if self.held:
            self.held = False
            self._on_release()

    def cancel_down(self) -> None:
        if self.held and self._on_cancel is not None:
            self.held = False
            self._on_cancel()


class PynputPushToTalk(HotkeyListener):
    def __init__(self, key_name: str = "f9", cancel_key: str = "esc") -> None:
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
        self._cancel = self._resolve_key(cancel_key) if cancel_key else None

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

    def run(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        keyboard = self._keyboard
        core = PushToTalkCore(on_press, on_release, on_cancel)

        def handle_press(key) -> None:
            if key == self._target:
                core.key_down()
            elif self._cancel is not None and key == self._cancel:
                core.cancel_down()

        def handle_release(key) -> None:
            if key == self._target:
                core.key_up()

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
