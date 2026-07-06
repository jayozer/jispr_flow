"""Global hotkey interface and the pynput implementation.

Platform caveats (see README): macOS requires Accessibility + Input
Monitoring permissions; Wayland sessions usually cannot observe global
hotkeys at all, so run hands-free mode there instead.
"""

from __future__ import annotations

import queue
import sys
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable

from local_flow.config import Config
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
        self._suppressed = False  # key still physically down after a cancel

    def key_down(self) -> None:
        if not self.held and not self._suppressed:
            self.held = True
            self._on_press()

    def key_up(self) -> None:
        self._suppressed = False
        if self.held:
            self.held = False
            self._on_release()

    def cancel_down(self) -> None:
        if self.held and self._on_cancel is not None:
            self.held = False
            self._suppressed = True  # swallow auto-repeats until physical release
            self._on_cancel()


class CallbackDispatcher:
    """Runs hotkey callbacks on a worker thread so OS event hooks return fast.

    Dictation stop does seconds of work (ASR + LLM polish + insertion);
    running it inside a Windows low-level keyboard hook or a macOS event-tap
    callback gets the hook/tap disabled by the OS. A single worker preserves
    press -> release ordering.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        thread = threading.Thread(target=self._worker, daemon=True)
        thread.start()

    def _worker(self) -> None:
        while True:
            fn = self._queue.get()
            try:
                fn()
            except Exception as exc:  # a failing callback must not kill dispatch
                print(f"hotkey callback failed: {exc}", file=sys.stderr)

    def wrap(self, fn: Callable[[], None] | None) -> Callable[[], None] | None:
        if fn is None:
            return None

        def enqueue() -> None:
            self._queue.put(fn)

        return enqueue


def resolve_key(keyboard, key_name: str):
    """Resolve a config key name to a pynput ``Key``/``KeyCode``.

    Shared by ``PynputPushToTalk`` and ``SpacePushToTalk`` (for the cancel
    key) so both backends accept special names (``esc``, ``f9``, ...) and
    single characters identically.
    """
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
        return resolve_key(self._keyboard, key_name)

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


def create_hotkey_listener(config: Config) -> HotkeyListener:
    """Build the push-to-talk listener for ``config.hotkey``.

    ``fn`` needs a macOS-only Quartz event tap (the Fn key never reaches
    other OSes); ``space`` needs per-event suppression, which pynput cannot
    do on Linux; anything else is a plain pynput key name.
    """
    name = config.hotkey.lower()
    if name == "fn":
        if sys.platform != "darwin":
            raise HotkeyBackendMissingError(
                "The Fn key can only be observed on macOS.",
                hint="On this platform Fn is handled by keyboard firmware and "
                "never reaches the OS. Set LOCAL_FLOW_HOTKEY to another key, "
                "e.g. f9 or space.",
            )
        import local_flow.hotkeys.macos_fn as macos_fn

        return macos_fn.QuartzFnListener(cancel_key=config.cancel_hotkey)
    if name == "space":
        if sys.platform.startswith("linux"):
            raise HotkeyBackendMissingError(
                "Space push-to-talk needs per-event key suppression, which is "
                "not possible on Linux/X11.",
                hint="Use another key (LOCAL_FLOW_HOTKEY=f9) or hands-free "
                "mode (LOCAL_FLOW_MODE=hands-free).",
            )
        import local_flow.hotkeys.space as space_mod

        return space_mod.SpacePushToTalk(
            hold_ms=config.hotkey_space_hold_ms, cancel_key=config.cancel_hotkey
        )
    return PynputPushToTalk(name, cancel_key=config.cancel_hotkey)
