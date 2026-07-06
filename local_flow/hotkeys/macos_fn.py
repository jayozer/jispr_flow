"""Fn-key push-to-talk on macOS via a listen-only Quartz event tap.

pynput has no ``Key.fn``: the Fn key arrives as a ``flagsChanged`` event
(keycode 63) whose ``kCGEventFlagMaskSecondaryFn`` bit tracks the key
state. ``FnLogic`` is pure so CI can drive it with fake events; only
``QuartzFnListener.run`` touches Quartz.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from local_flow.errors import HotkeyBackendMissingError
from local_flow.hotkeys.base import HotkeyListener, PushToTalkCore

FN_KEYCODE = 63  # kVK_Function
ESCAPE_KEYCODE = 53  # kVK_Escape
_CANCEL_KEYCODES = {"esc": ESCAPE_KEYCODE}


class FnLogic:
    """Derives press/release/cancel from flagsChanged + keyDown events."""

    def __init__(self, core: PushToTalkCore, cancel_keycode: int | None) -> None:
        self._core = core
        self._cancel_keycode = cancel_keycode
        self._fn_down = False

    def flags_changed(self, fn_active: bool) -> None:
        if fn_active and not self._fn_down:
            self._fn_down = True
            self._core.key_down()
        elif not fn_active and self._fn_down:
            self._fn_down = False
            self._core.key_up()

    def key_down(self, keycode: int) -> None:
        if self._cancel_keycode is not None and keycode == self._cancel_keycode:
            self._core.cancel_down()


class QuartzFnListener(HotkeyListener):
    def __init__(self, cancel_key: str = "esc") -> None:
        if sys.platform != "darwin":
            raise HotkeyBackendMissingError(
                "The Fn key can only be observed on macOS.",
                hint="Set LOCAL_FLOW_HOTKEY to another key, e.g. f9 or space.",
            )
        # Validate before the Quartz import so a bad cancel key fails loudly
        # (and headlessly testable) instead of silently disabling cancel.
        if cancel_key and cancel_key.lower() not in _CANCEL_KEYCODES:
            raise HotkeyBackendMissingError(
                f"The fn hotkey backend only supports 'esc' as the cancel key, "
                f"not {cancel_key!r}.",
                hint="Set LOCAL_FLOW_CANCEL_HOTKEY=esc, or choose a different hotkey.",
            )
        try:
            import Quartz
        except ImportError as exc:
            raise HotkeyBackendMissingError(
                "pyobjc-framework-Quartz is not installed.",
                hint="Install desktop extras: uv sync --extra desktop.",
            ) from exc
        self._quartz = Quartz
        self._cancel_keycode = _CANCEL_KEYCODES[cancel_key.lower()] if cancel_key else None

    def run(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        q = self._quartz
        logic = FnLogic(PushToTalkCore(on_press, on_release, on_cancel), self._cancel_keycode)
        tap_holder: list = []

        def callback(_proxy, event_type, event, _refcon):
            if event_type in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
                if tap_holder:
                    q.CGEventTapEnable(tap_holder[0], True)
                return event
            keycode = q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode)
            if event_type == q.kCGEventFlagsChanged:
                if keycode == FN_KEYCODE:
                    flags = q.CGEventGetFlags(event)
                    logic.flags_changed(bool(flags & q.kCGEventFlagMaskSecondaryFn))
            elif event_type == q.kCGEventKeyDown:
                logic.key_down(keycode)
            return event

        mask = q.CGEventMaskBit(q.kCGEventFlagsChanged) | q.CGEventMaskBit(q.kCGEventKeyDown)
        tap = q.CGEventTapCreate(
            q.kCGSessionEventTap,
            q.kCGHeadInsertEventTap,
            q.kCGEventTapOptionListenOnly,
            mask,
            callback,
            None,
        )
        if tap is None:
            raise HotkeyBackendMissingError(
                "Could not create the macOS event tap for the Fn key.",
                hint="Grant Accessibility AND Input Monitoring permission to "
                "your terminal in System Settings > Privacy & Security, then "
                "restart the terminal.",
            )
        tap_holder.append(tap)
        source = q.CFMachPortCreateRunLoopSource(None, tap, 0)
        q.CFRunLoopAddSource(q.CFRunLoopGetCurrent(), source, q.kCFRunLoopCommonModes)
        q.CGEventTapEnable(tap, True)
        q.CFRunLoopRun()
