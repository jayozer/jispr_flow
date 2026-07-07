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

    ``cancel_gate``: an app-level escape hatch so this listener's cancel key
    can discard a recording that a *different* listener started (e.g. Esc on
    the keyboard discarding a mouse-started recording). Without it, cancel is
    gated on this instance's own ``held`` -- which is False when the mouse,
    not this keyboard listener, started the recording, so Esc would silently
    no-op. When the gate fires cancel while NOT held, no key of this
    listener's own is actually down, so ``held``/``_suppressed`` are left
    untouched -- there is nothing of this listener's to swallow.
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
        cancel_gate: Callable[[], bool] | None = None,
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._on_cancel = on_cancel
        self._cancel_gate = cancel_gate
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
        if self._on_cancel is None:
            return
        if self.held:
            self.held = False
            self._suppressed = True  # swallow auto-repeats until physical release
            self._on_cancel()
        elif self._cancel_gate is not None and self._cancel_gate():
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
    def __init__(
        self,
        key_name: str = "f9",
        cancel_key: str = "esc",
        cancel_gate: Callable[[], bool] | None = None,
    ) -> None:
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
        self._cancel_gate = cancel_gate

    def _resolve_key(self, key_name: str):
        return resolve_key(self._keyboard, key_name)

    def run(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        keyboard = self._keyboard
        core = PushToTalkCore(on_press, on_release, on_cancel, self._cancel_gate)

        def handle_press(key, injected=False) -> None:
            # Synthetic keystrokes (our own TypingSink typing a transcript, or
            # any other injector) must not start, stop, or cancel a
            # dictation -- same invariant as SpacePushToTalk's guards.
            if injected:
                return
            if key == self._target:
                core.key_down()
            elif self._cancel is not None and key == self._cancel:
                core.cancel_down()

        def handle_release(key, injected=False) -> None:
            if injected:
                return
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


class TapListener:
    """Fires ``on_tap`` on every press (tap) of one key -- no hold semantics.

    Used by the transform hotkey (see ``local_flow.app._run_loop``): a single
    tap should immediately trigger "capture selection -> transform ->
    replace", unlike push-to-talk's press-to-start/release-to-stop. This
    deliberately does not subclass :class:`HotkeyListener` -- its callback
    shape (``run(on_tap)``, one zero-arg callback fired on key-down only) is
    different from ``run(on_press, on_release, on_cancel)`` -- and needs none
    of :class:`PushToTalkCore`'s held-state bookkeeping.
    """

    def __init__(self, key_name: str) -> None:
        try:
            from pynput import keyboard
        except ImportError as exc:
            raise HotkeyBackendMissingError(
                "The 'pynput' package is not installed.",
                hint="Install desktop extras: uv sync --extra desktop.",
            ) from exc
        self._keyboard = keyboard
        self.key_name = key_name
        self._target = resolve_key(keyboard, key_name)
        self._on_tap: Callable[[], None] | None = None

    def handle_press(self, key, injected: bool = False) -> None:
        """Process one key-press event.

        A plain instance method (not a closure built inside ``run``) so
        tests can call it directly with any sentinel key object -- compared
        only by identity against ``self._target`` -- without a live pynput
        listener, mirroring ``MousePushToTalk.handle_click``. Synthetic key
        events (e.g. this process's own ``TypingSink`` typing a transcript)
        must never trigger a transform, same ``injected`` guard invariant as
        every other hotkey listener.
        """
        if injected:
            return
        if key == self._target and self._on_tap is not None:
            self._on_tap()

    def run(self, on_tap: Callable[[], None]) -> None:
        self._on_tap = on_tap
        keyboard = self._keyboard
        try:
            with keyboard.Listener(on_press=self.handle_press) as listener:
                listener.join()
        except Exception as exc:
            raise HotkeyBackendMissingError(
                f"The global hotkey listener failed: {exc}",
                hint="macOS: grant Accessibility AND Input Monitoring permission "
                "to your terminal. Linux/Wayland: global key capture is blocked "
                "by the compositor - use hands-free mode "
                "(LOCAL_FLOW_MODE=hands-free) instead.",
            ) from exc


def create_hotkey_listener(
    config: Config, cancel_gate: Callable[[], bool] | None = None
) -> HotkeyListener:
    """Build the push-to-talk listener for ``config.hotkey``.

    ``fn`` needs a macOS-only Quartz event tap (the Fn key never reaches
    other OSes); ``space`` needs per-event suppression, which pynput cannot
    do on Linux; anything else is a plain pynput key name.

    ``cancel_gate``, when given, lets the cancel key discard a recording
    started by a *different* listener (e.g. mouse push-to-talk) even though
    this listener's own key was never held -- see ``PushToTalkCore``.
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

        return macos_fn.QuartzFnListener(
            cancel_key=config.cancel_hotkey, cancel_gate=cancel_gate
        )
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
            hold_ms=config.hotkey_space_hold_ms,
            cancel_key=config.cancel_hotkey,
            cancel_gate=cancel_gate,
        )
    return PynputPushToTalk(name, cancel_key=config.cancel_hotkey, cancel_gate=cancel_gate)


def create_mouse_listener(config: Config) -> HotkeyListener | None:
    """Build the mouse-button push-to-talk listener, or ``None`` when unset.

    Mouse push-to-talk is opt-in (``config.mouse_button`` defaults to
    ``""``): the common case pays nothing beyond this check. A listener is
    built when *either* ``mouse_button`` or ``mouse_enter_button`` is set --
    an "enter-only" config (``mouse_button`` empty, only
    ``mouse_enter_button`` set) still needs one, just with push-to-talk
    inactive (see ``MousePushToTalk``'s ``button=None``).
    ``local_flow.hotkeys.mouse`` is imported as a module (not
    ``from ... import MousePushToTalk``) so tests can monkeypatch
    ``local_flow.hotkeys.mouse.MousePushToTalk``, the same way
    ``create_hotkey_listener`` is tested against ``space``/``macos_fn``.
    ``config.mouse_button``/``mouse_mode``/``mouse_enter_button`` are
    already validated (left/right rejected, hold/toggle checked, the two
    buttons distinct) by ``load_config``, so no further validation happens
    here.
    """
    if not config.mouse_button and not config.mouse_enter_button:
        return None
    import local_flow.hotkeys.mouse as mouse_mod

    return mouse_mod.MousePushToTalk(
        button=config.mouse_button or None,
        mode=config.mouse_mode,
        enter_button=config.mouse_enter_button,
    )
