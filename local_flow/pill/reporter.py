"""StatusReporter that drives a floating pill surface."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from local_flow.pill.state import PillStateMachine, PillView
from local_flow.status import State, StatusReporter


class PillSurface(Protocol):
    def render(self, view: PillView) -> None:
        """Render one immutable pill view on the UI thread."""


class PillReporter(StatusReporter):
    """Thread-safe bridge from capture/status threads to a UI surface."""

    wants_audio_level = True

    def __init__(
        self,
        surface: PillSurface,
        state_machine: PillStateMachine | None = None,
        dispatch: Callable[[Callable[[], None]], None] | None = None,
        dispatch_later: Callable[[float, Callable[[], None]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_level_fps: float = 20.0,
        flash_seconds: float = 0.8,
    ) -> None:
        self._surface = surface
        self._state_machine = state_machine or PillStateMachine()
        self._dispatch = dispatch or (lambda action: action())
        self._dispatch_later = dispatch_later
        self._clock = clock
        self._min_level_interval = 1.0 / max(1.0, max_level_fps)
        self._last_level_at = float("-inf")
        self._revision = 0
        self._flash_seconds = flash_seconds
        self._lock = threading.Lock()

    def _render(self, view: PillView) -> None:
        def apply() -> None:
            try:
                self._surface.render(view)
            except Exception:
                # The pill is best-effort product chrome. It must never stop
                # capture, transcription, or insertion.
                pass

        self._dispatch(apply)

    def notify(self, state: State, detail: str = "") -> None:
        with self._lock:
            if (
                state == "idle"
                and self._state_machine.view.kind in ("inserted", "error")
                and self._dispatch_later is not None
            ):
                revision = self._revision

                def return_to_idle() -> None:
                    with self._lock:
                        if self._revision != revision:
                            return
                        view = self._state_machine.apply("idle")
                        self._revision += 1
                    self._render(view)

                self._dispatch_later(self._flash_seconds, return_to_idle)
                return
            self._revision += 1
            view = self._state_machine.apply(state, detail)
            if state == "recording":
                self._last_level_at = float("-inf")
        self._render(view)

    def audio_level(self, level: float) -> None:
        now = self._clock()
        with self._lock:
            if now - self._last_level_at < self._min_level_interval:
                return
            view = self._state_machine.set_level(level)
            if view.kind != "recording":
                return
            self._last_level_at = now
        self._render(view)
