"""Tray state machine and icon generation for the pystray-based menu-bar app.

Pure logic lives in :mod:`local_flow.tray.state`; Pillow-based icon
rendering lives in :mod:`local_flow.tray.icons` (PIL is imported lazily, so
importing this package never requires the ``tray`` extra). The pystray glue
itself lands in Task 3's ``local_flow.tray.app``.
"""

from local_flow.tray.icons import draw_icon
from local_flow.tray.state import TrayStateMachine, TrayView

__all__ = ["TrayStateMachine", "TrayView", "draw_icon"]
