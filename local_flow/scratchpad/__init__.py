"""Local, plain-markdown scratchpad notes (E13): NoteStore + ScratchpadSink
+ ScratchpadWindow.

`ScratchpadWindow`'s import here is safe even without a Tk-enabled Python:
its module only imports `tkinter` lazily, inside `ScratchpadWindow.__init__`.
"""

from local_flow.scratchpad.sink import ScratchpadSink
from local_flow.scratchpad.store import NoteStore
from local_flow.scratchpad.window import ScratchpadWindow

__all__ = ["NoteStore", "ScratchpadSink", "ScratchpadWindow"]
