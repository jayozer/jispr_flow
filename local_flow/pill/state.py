"""Toolkit-free state mapping for the floating recording pill."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from local_flow.status import State

PillKind = Literal["idle", "recording", "processing", "inserted", "error"]
_DETAIL_LIMIT = 34


@dataclass(frozen=True)
class PillView:
    """Everything a visual pill surface needs to render one frame."""

    kind: PillKind
    label: str
    level: float = 0.0
    show_meter: bool = False


class PillStateMachine:
    """Map run-loop status and live mic levels to a compact pill view."""

    def __init__(self, hotkey: str = "fn") -> None:
        display_key = "Fn" if hotkey.lower() == "fn" else hotkey.upper()
        self._idle = PillView("idle", f"Ready · Hold {display_key}")
        self._view = self._idle

    @property
    def view(self) -> PillView:
        return self._view
    def apply(self, state: State, detail: str = "") -> PillView:
        if state == "recording":
            self._view = PillView("recording", "Listening", show_meter=True)
        elif state in ("processing", "preview"):
            self._view = PillView("processing", "Transcribing…")
        elif state == "inserted":
            self._view = PillView("inserted", "Inserted")
        elif state in ("error", "warning"):
            label = detail.strip()[:_DETAIL_LIMIT] or state.capitalize()
            self._view = PillView("error", label)
        else:
            self._view = self._idle
        return self._view

    def set_level(self, level: float) -> PillView:
        if self._view.kind != "recording":
            return self._view
        self._view = replace(self._view, level=max(0.0, min(1.0, level)))
        return self._view
