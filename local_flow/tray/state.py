"""Tray state machine: pure mapping from run-loop ``State`` to tray display.

No Pillow or pystray imports here — see :mod:`local_flow.tray.icons` for icon
rendering and (Task 3) :mod:`local_flow.tray.app` for the pystray glue.
Keeping this module free of GUI-toolkit imports means the mapping is testable
without the ``tray`` extra installed.
"""

from __future__ import annotations

from dataclasses import dataclass

from local_flow.status import State

_TOOLTIP_TRUNCATE = 40


@dataclass(frozen=True)
class TrayView:
    """What the tray icon should look like right now."""

    icon: str  # "idle" | "recording" | "processing" | "error"
    tooltip: str
    flash: bool = False  # transient (e.g. "inserted") that reverts to idle


class TrayStateMachine:
    """Maps each dictation :data:`~local_flow.status.State` to a `TrayView`.

    Each call only depends on its own arguments — there is no carried-over
    "sticky" error state. ``warning`` shows the error icon for exactly one
    view; the very next ``apply()`` call, whatever state it carries, wins
    outright.
    """

    def apply(self, state: State, detail: str = "") -> TrayView:
        if state == "recording":
            return TrayView(icon="recording", tooltip="local-flow — recording")
        if state == "processing":
            return TrayView(icon="processing", tooltip="local-flow — processing")
        if state == "inserted":
            return TrayView(
                icon="idle",
                tooltip=f"inserted: {detail[:_TOOLTIP_TRUNCATE]}",
                flash=True,
            )
        if state in ("error", "warning"):
            tooltip = f"local-flow — {state}: {detail}" if detail else f"local-flow — {state}"
            return TrayView(icon="error", tooltip=tooltip)
        # "idle" (and any unrecognized state) -> idle view.
        return TrayView(icon="idle", tooltip="local-flow — idle")
