"""ScratchpadSink: a TextSink that lands dictation in a NoteStore note."""

from __future__ import annotations

from local_flow.insertion.base import TextSink
from local_flow.scratchpad.store import NoteStore


class ScratchpadSink(TextSink):
    """Routes ``insert``/``press_key`` into a :class:`NoteStore` note instead
    of the desktop -- swap this in for the configured sink and dictation
    lands in the active scratchpad note rather than the frontmost app.

    ``press_key("enter")`` is intentionally a no-op, not an oversight:
    :meth:`NoteStore.append` already prefixes a blank line onto any append
    made to a non-empty note, so every dictated utterance is already
    paragraph-separated from the one before it with no help needed from an
    explicit "press enter"/"new paragraph" gesture. If this method wrote
    anything for "enter" (even an empty append), the next utterance's
    ``insert`` would double up that separator (two blank lines instead of
    one). So "enter" and any other key both do nothing here -- the same
    outward behavior -- but "enter" is called out explicitly below because
    its no-op-ness is a deliberate design choice (see above), distinct in
    *intent* from the truly-unrecognized keys that fall through to
    ``TextSink``'s own default no-op.
    """

    name = "scratchpad"

    def __init__(self, store: NoteStore) -> None:
        self.store = store

    def insert(self, text: str) -> None:
        self.store.append(text)

    def press_key(self, key: str) -> None:
        if key == "enter":
            # See class docstring: NoteStore.append's own blank-line rule
            # already provides paragraph separation, so there is nothing to
            # do here -- this is a deliberate no-op, not a missing feature.
            return
