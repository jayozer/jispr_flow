"""pystray glue: ``local-flow tray``, a menu-bar app over the same run-loop.

``pystray``/``Pillow`` are imported lazily (only inside :class:`TrayApp`'s
constructor and the small helpers that need them), so importing this module
never requires the ``tray`` extra -- only constructing/running a `TrayApp`
does. Everything else here (``parse_languages``, :class:`MenuEntry`,
:func:`build_menu`, :class:`TrayReporter`) is pure/GUI-toolkit-free and
tested headlessly; the pystray `Menu`/`Icon` wiring itself is manual-verify
only (see the README's "Tray app" section).
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from local_flow.config import Config
from local_flow.errors import LocalFlowError
from local_flow.status import State, StatusReporter
from local_flow.tray.icons import draw_icon
from local_flow.tray.state import TrayStateMachine

# Best-effort wait for the loop thread to notice a stop request and exit,
# used by both `_start_loop` (double-start guard) and `_stop_loop`. A module
# constant (rather than a literal) so tests can monkeypatch it down to keep
# the double-start guard test fast.
_JOIN_TIMEOUT = 2.0


def parse_languages(raw: str) -> list[str]:
    """Parse ``config.languages`` (e.g. ``"en, de,tr"``) into ``["en", "de", "tr"]``.

    Whitespace around each code is stripped, blank entries (including an
    empty or whitespace-only ``raw``) are dropped, and duplicates are removed
    while preserving first-seen order.
    """
    seen: set[str] = set()
    codes: list[str] = []
    for piece in raw.split(","):
        code = piece.strip()
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


@dataclass(frozen=True)
class MenuEntry:
    """One pystray-agnostic menu node.

    :class:`TrayApp` converts a tree of these into real ``pystray.MenuItem``
    objects; keeping the structure itself free of pystray imports means menu
    *contents* (labels, order, which item is checked) are testable without
    the ``tray`` extra installed.
    """

    label: str
    action: Callable[[], None] | None = None
    submenu: tuple[MenuEntry, ...] | None = None
    checked: bool | None = None  # None = not a checkable item
    enabled: bool = True


def build_menu(
    *,
    mode: str,
    running: bool,
    toggle_running: Callable[[], None],
    style_names: list[str],
    current_style: str,
    set_style: Callable[[str], None],
    languages: list[str],
    current_language: str | None,
    set_language: Callable[[str], None],
    open_data_folder: Callable[[], None],
    quit_app: Callable[[], None],
) -> tuple[MenuEntry, ...]:
    """Pure construction of the tray menu tree (no pystray import here).

    - ``Dictation``: in hands-free mode, a real Start/Stop toggle over the
      loop thread; in push-to-talk mode, a disabled status label (there is
      no equivalent "loop" to start/stop -- the hotkey listener owns that).
    - ``Mode``: a disabled label showing the configured capture mode.
    - ``Style``/``Language`` submenus are omitted entirely when there is
      nothing to offer (``style_names``/``languages`` empty).
    """
    if mode == "hands-free":
        dictation_entry = MenuEntry(
            label="Stop dictation" if running else "Start dictation",
            action=toggle_running,
        )
    else:
        dictation_entry = MenuEntry(
            label="Dictation: listening for hotkey", action=None, enabled=False
        )

    entries: list[MenuEntry] = [
        dictation_entry,
        MenuEntry(label=f"Mode: {mode}", action=None, enabled=False),
    ]

    if style_names:
        entries.append(
            MenuEntry(
                label="Style",
                submenu=tuple(
                    MenuEntry(
                        label=name,
                        action=lambda name=name: set_style(name),
                        checked=(name == current_style),
                    )
                    for name in style_names
                ),
            )
        )

    if languages:
        entries.append(
            MenuEntry(
                label="Language",
                submenu=tuple(
                    MenuEntry(
                        label=code,
                        action=lambda code=code: set_language(code),
                        checked=(code == current_language),
                    )
                    for code in languages
                ),
            )
        )

    entries.append(MenuEntry(label="Open data folder", action=open_data_folder))
    entries.append(MenuEntry(label="Quit", action=quit_app))
    return tuple(entries)


def _dispatch_to_main_thread(func: Callable[[], None]) -> None:
    """Default :class:`TrayReporter` dispatch: run ``func`` on the main thread.

    ``notify`` fires on the dictation-loop thread, but pystray's darwin
    backend drives AppKit when ``.icon``/``.title`` are assigned, and AppKit
    is only safe to touch from the main thread (which is blocked inside
    ``TrayApp.run``'s ``icon.run()`` -- the Cocoa run loop). On macOS this
    queues ``func`` onto that run loop via PyObjC (a pystray dependency
    there); calls already on the main thread, other platforms, and a
    missing/failing PyObjC all just run inline -- the pre-marshaling
    behavior, so degraded is never worse than before.
    """
    if threading.current_thread() is threading.main_thread():
        func()
        return
    if sys.platform == "darwin":
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(func)
            return
        except Exception:
            pass  # PyObjC missing/unusable -> apply inline, as before
    func()


class TrayReporter(StatusReporter):
    """Applies dictation-loop state transitions to a pystray-like icon.

    Takes the icon instance (real ``pystray.Icon`` or a test double exposing
    the same ``.icon``/``.title``/``.notify(str)`` surface) plus a
    :class:`~local_flow.tray.state.TrayStateMachine`, so this is fully
    testable by injecting a fake icon recorder -- no pystray/Pillow needed
    except to actually render the icon image inside :func:`notify`.

    Every icon mutation is routed through ``dispatch`` (default:
    :func:`_dispatch_to_main_thread`) rather than applied inline, because
    ``notify`` runs on the dictation-loop thread while the icon backend is
    only main-thread-safe. Re-rendering the icon image is skipped when the
    mapped icon *kind* is unchanged (streaming previews re-notify the same
    "processing" kind on every partial transcript); the tooltip still
    updates every time.
    """

    def __init__(
        self,
        icon: object,
        state_machine: TrayStateMachine | None = None,
        dispatch: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._icon = icon
        self._state_machine = state_machine or TrayStateMachine()
        self._dispatch = dispatch or _dispatch_to_main_thread
        # Only ever read/written inside dispatched closures, which the
        # dispatcher runs serially on one thread -- no lock needed.
        self._last_icon_kind: str | None = None

    def notify(self, state: State, detail: str = "") -> None:
        view = self._state_machine.apply(state, detail)

        def apply_view() -> None:
            try:
                if view.icon != self._last_icon_kind:
                    self._icon.icon = draw_icon(view.icon)
                    # Recorded only after the assignment succeeds, so a
                    # swallowed backend failure is retried next notify
                    # instead of leaving the icon stale forever.
                    self._last_icon_kind = view.icon
                self._icon.title = view.tooltip
            except Exception:
                pass  # icon redraws are best-effort; must never crash dictation
            if state in ("error", "warning"):
                try:
                    self._icon.notify(detail or view.tooltip)
                except Exception:
                    pass  # desktop notifications are best-effort (not all backends support them)

        self._dispatch(apply_view)


def _open_folder(path: Path) -> None:
    """Open ``path`` in the platform file manager (best-effort, lazy import)."""
    import subprocess

    if sys.platform == "darwin":
        cmd = ["open", str(path)]
    elif sys.platform.startswith("win"):
        cmd = ["explorer", str(path)]
    else:
        cmd = ["xdg-open", str(path)]
    try:
        subprocess.run(cmd, check=False)
    except OSError:
        pass  # best-effort; a missing file manager binary is not a tray error


def _to_pystray_item(entry: MenuEntry):
    import pystray

    if entry.submenu is not None:
        return pystray.MenuItem(
            entry.label,
            pystray.Menu(*(_to_pystray_item(sub) for sub in entry.submenu)),
            enabled=entry.enabled,
        )
    checked = entry.checked
    return pystray.MenuItem(
        entry.label,
        entry.action,
        checked=(lambda item, v=checked: v) if checked is not None else None,
        radio=checked is not None,
        enabled=entry.enabled,
    )


def _to_pystray_menu(entries: tuple[MenuEntry, ...]):
    import pystray

    return pystray.Menu(*(_to_pystray_item(entry) for entry in entries))


class TrayApp:
    """pystray glue over the same run-loop machinery as ``local-flow run``.

    ``pystray``/``Pillow`` are imported lazily here (not at module import
    time) so importing :mod:`local_flow.tray.app` never requires the
    ``tray`` extra; only constructing a `TrayApp` does -- a missing extra
    raises :class:`~local_flow.errors.LocalFlowError` with a fix-it hint,
    matching every other optional-backend adapter in this codebase.
    """

    def __init__(self, config: Config) -> None:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError as exc:
            raise LocalFlowError(
                "The 'pystray'/'pillow' packages are not installed.",
                hint="Install tray extras: uv sync --extra tray",
            ) from exc

        # Deferred: `local_flow.app` imports this module lazily inside
        # `_cmd_tray`, so importing it back here at call time (not module
        # import time) avoids a circular import.
        from local_flow.app import _build_run_dependencies

        self.config = config
        self.mode = config.mode
        self._deps = _build_run_dependencies(config)
        self.pipeline = self._deps.pipeline
        self.source = self._deps.source
        self.vad = self._deps.vad
        self.pending_store = self._deps.pending_store
        self._languages = parse_languages(config.languages)

        self._running = False
        self._stop_event = threading.Event()
        self._loop_thread: threading.Thread | None = None

        self.icon = self._build_icon()
        self.reporter = TrayReporter(self.icon, TrayStateMachine())

    def _build_icon(self):
        import pystray

        icon = pystray.Icon("local-flow", draw_icon("idle"), "local-flow — idle")
        icon.menu = self._build_pystray_menu()
        return icon

    def _build_pystray_menu(self):
        entries = build_menu(
            mode=self.mode,
            running=self._running,
            toggle_running=self._toggle_dictation,
            style_names=sorted(self.pipeline.store.styles()),
            current_style=self.pipeline.polisher.style,
            set_style=self._set_style,
            languages=self._languages,
            current_language=self.pipeline.transcriber.language,
            set_language=self._set_language,
            open_data_folder=self._open_data_folder,
            quit_app=self._quit,
        )
        return _to_pystray_menu(entries)

    def _refresh_menu(self) -> None:
        self.icon.menu = self._build_pystray_menu()

    def _start_loop(self) -> None:
        if self._running:
            return
        if self._loop_thread is not None and self._loop_thread.is_alive():
            # A previous loop thread hasn't exited yet -- e.g. Stop was
            # clicked (which set `_running = False`) and Start was clicked
            # again before that thread noticed its stop event and wound
            # down. Wait briefly; if it's still alive after that, refuse to
            # start a second thread -- two concurrent `_run_loop`s would
            # both grab the microphone (double capture / PortAudio
            # device-busy).
            self._loop_thread.join(timeout=_JOIN_TIMEOUT)
            if self._loop_thread.is_alive():
                return

        from local_flow.app import _run_loop

        self._stop_event = threading.Event()
        self._loop_thread = threading.Thread(
            target=_run_loop,
            args=(
                self.config,
                self.mode,
                self.reporter,
                self._stop_event,
                self._deps,
            ),
            daemon=True,
        )
        self._loop_thread.start()
        self._running = True

    def _stop_loop(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        self._running = False
        # Best-effort: the loop thread is a daemon, so never block forever
        # waiting for it -- but do give it a moment to actually let go of
        # the microphone before we might be asked to start a new one.
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=_JOIN_TIMEOUT)
        # The loop thread exits silently once it notices the stop event --
        # it never emits a final state, so without this the icon stayed
        # stuck on whatever it last showed (usually red "recording"). After
        # the join so an in-flight notify from the winding-down thread
        # can't land later and overwrite it.
        self.reporter.notify("idle")

    def _toggle_dictation(self) -> None:
        # Push-to-talk has no loop-level Start/Stop concept (the hotkey
        # listener already owns press/release per utterance); `build_menu`
        # keeps that menu item disabled, but guard here too in case a stale
        # menu reference fires this callback.
        if self.mode != "hands-free":
            return
        if self._running:
            self._stop_loop()
        else:
            self._start_loop()
        self._refresh_menu()

    def _set_style(self, name: str) -> None:
        self.pipeline.polisher.style = name
        self._refresh_menu()

    def _set_language(self, code: str) -> None:
        self.pipeline.transcriber.language = code
        self._refresh_menu()

    def _open_data_folder(self) -> None:
        _open_folder(self.pipeline.store.data_dir)

    def _quit(self) -> None:
        self._stop_loop()
        self.icon.stop()

    def run(self) -> None:
        """Start the dictation loop, then block running the pystray icon.

        Manual-verify only -- see the README's "Tray app" section and its
        manual checklist.
        """
        self._start_loop()
        self.icon.run()
