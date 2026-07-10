"""Mouse-button push-to-talk: hold (or click to toggle) a non-primary button.

Runs alongside the keyboard hotkey listener (see ``local_flow.app._run_loop``)
rather than replacing it: cancel is handled by the keyboard listener's cancel
key, never here (using both at once is the user's own foot-gun -- documented
in README).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from local_flow.errors import HotkeyBackendMissingError
from local_flow.hotkeys.base import HotkeyListener, PushToTalkCore

_MOUSE_BUTTON_NAMES = ("middle", "x1", "x2")


class MouseToggleMachine:
    """Pure alternating start/stop state for ``mode="toggle"``.

    One click starts a recording, the next click stops it -- no timers, no
    press/release distinction (a release is simply ignored by the caller).
    """

    def __init__(self) -> None:
        self._recording = False

    def click(self) -> str:
        self._recording = not self._recording
        return "start" if self._recording else "stop"


def resolve_mouse_button(mouse, name: str):
    """Resolve a config button name to a ``pynput.mouse.Button``.

    Only ``middle``/``x1``/``x2`` are ever passed here -- ``left``/``right``
    are rejected earlier, at config load (see ``local_flow.config`), since
    they're needed for normal clicking. ``x1``/``x2`` (the side/back-forward
    buttons) are exposed by pynput's Windows and X11 backends but not its
    macOS (Quartz) backend, so resolving them there raises with an
    actionable hint instead of an ``AttributeError``.
    """
    if name not in _MOUSE_BUTTON_NAMES:
        raise HotkeyBackendMissingError(
            f"Unsupported mouse button {name!r}.",
            hint=f"Use one of: {', '.join(_MOUSE_BUTTON_NAMES)}.",
        )
    button = getattr(mouse.Button, name, None)
    if button is None:
        raise HotkeyBackendMissingError(
            f"pynput does not expose mouse button {name!r} on this platform.",
            hint="x1/x2 (side buttons) are only available on pynput's "
            "Windows/X11 backends; use button=middle, or a different OS.",
        )
    return button


class MousePushToTalk(HotkeyListener):
    """Push-to-talk driven by a click of a non-primary mouse button.

    ``mode="hold"`` (default): press-and-hold ``button`` starts/stops
    dictation exactly like a keyboard push-to-talk key, via the shared
    ``PushToTalkCore``. ``mode="toggle"``: each press of ``button``
    alternates start/stop (``MouseToggleMachine``); releases are ignored.

    ``enter_button``/``on_enter``: an independent, always-on click handler
    that fires ``on_enter`` on every press of a second configured button,
    regardless of ``mode`` -- wired by ``local_flow.app._run_loop`` to press
    Enter through the configured sink.

    ``button=None`` (an "enter-only" configuration: ``mouse_button`` unset,
    only ``mouse_enter_button`` set) leaves push-to-talk inactive -- no real
    button ever compares equal to ``None`` in ``handle_click`` -- while
    ``enter_button`` still works normally. See ``create_mouse_listener``.
    """

    def __init__(
        self,
        button: str | None = None,
        mode: str = "hold",
        enter_button: str = "",
        on_enter: Callable[[], None] | None = None,
    ) -> None:
        try:
            from pynput import mouse
        except ImportError as exc:
            raise HotkeyBackendMissingError(
                "The 'pynput' package is not installed.",
                hint="Install desktop extras: uv sync --extra desktop.",
            ) from exc
        self._mouse = mouse
        self.button = button
        self.mode = mode
        self.enter_button = enter_button
        self._target = resolve_mouse_button(mouse, button) if button else None
        self._enter_target = (
            resolve_mouse_button(mouse, enter_button) if enter_button else None
        )
        self.on_enter = on_enter
        self._toggle = MouseToggleMachine()
        self._on_press: Callable[[], None] | None = None
        self._on_release: Callable[[], None] | None = None
        self._core: PushToTalkCore | None = None
        self._listener = None
        self._stop_requested = threading.Event()

    def handle_click(self, x, y, button, pressed, injected: bool = False) -> None:
        """Process one mouse click event.

        A plain instance method (not a closure built inside ``run``) so
        tests can call it directly with any sentinel ``button`` object --
        compared only by identity against ``self._target``/
        ``self._enter_target`` -- without a live pynput listener.
        """
        # Synthetic clicks (there are none today, but future injectors might
        # exist) must not start, stop, or cancel a dictation -- same
        # invariant as the keyboard listeners' `injected` guards.
        if injected:
            return
        if self._enter_target is not None and button == self._enter_target:
            if pressed and self.on_enter is not None:
                self.on_enter()
            return
        if button != self._target:
            return
        if self.mode == "toggle":
            if not pressed:
                return
            # Resync note: a keyboard-driven cancel (the app-level
            # `cancel_gate` in `PushToTalkCore`/`SpacePushToTalk`) discards
            # the recording without going through `_toggle`, so this machine
            # still thinks recording is "on" afterward. The next click then
            # sends "stop" instead of "start" -- one extra click needed to
            # fully resync. Self-correcting, not a bug: `_run_loop.finish()`
            # already handles an empty capture gracefully
            # (`captured.pop("pcm", b"")`), so that stray "stop" just no-ops.
            action = self._toggle.click()
            if action == "start" and self._on_press is not None:
                self._on_press()
            elif action == "stop" and self._on_release is not None:
                self._on_release()
        else:
            if self._core is None:
                return
            if pressed:
                self._core.key_down()
            else:
                self._core.key_up()

    def run(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        """Block, driving ``on_press``/``on_release`` from clicks of ``button``.

        ``on_cancel`` is accepted for interface parity with ``HotkeyListener``
        but never invoked: mouse push-to-talk has no cancel gesture of its
        own (see class docstring) -- the keyboard listener's cancel key
        still works because both listeners run concurrently and share the
        same dispatcher-wrapped callbacks (see ``local_flow.app._run_loop``).
        """
        self._on_press = on_press
        self._on_release = on_release
        self._core = PushToTalkCore(on_press, on_release, None) if self.mode == "hold" else None
        mouse = self._mouse
        try:
            with mouse.Listener(on_click=self.handle_click) as listener:
                self._listener = listener
                if self._stop_requested.is_set():
                    listener.stop()
                listener.join()
        except Exception as exc:
            raise HotkeyBackendMissingError(
                f"The mouse hotkey listener failed: {exc}",
                hint="macOS: grant Accessibility AND Input Monitoring permission "
                "to your terminal.",
            ) from exc
        finally:
            self._listener = None

    def stop(self) -> None:
        self._stop_requested.set()
        listener = self._listener
        if listener is not None:
            listener.stop()
