"""macOS NSPasteboard snapshot/restore, full fidelity rather than text-only.

pyperclip (pbcopy/pbpaste) round-trips plain text only, so
:class:`~local_flow.transforms.selection.SelectionCapture`'s
save -> clear -> copy -> restore dance would silently destroy an image, file
list, or rich-text clipboard: the "save" would read ``""`` and the "restore"
would write ``""`` back. This module snapshots every pasteboard item with all
of its type representations and writes them back verbatim on restore.

Platform isolation: AppKit is imported lazily inside
:class:`DarwinPasteboard.__init__` (pyobjc-framework-Cocoa, part of the
``desktop`` extra, macOS only), never at module scope -- importing this
module stays safe on a bare headless machine, and the pure
:func:`snapshot_items`/:func:`restore_items` helpers operate on any
pasteboard-shaped object so tests cover them with fakes.
"""

from __future__ import annotations

from collections.abc import Callable

# One pasteboard item, as [(type identifier, opaque data), ...]; a snapshot
# is a list of such items. The data values (NSData in real use) are treated
# as opaque -- read from one pasteboard item, handed back to another.
PasteboardItems = list[list[tuple[str, object]]]


def snapshot_items(pasteboard) -> PasteboardItems:
    """Read every item's full (type, data) representations off ``pasteboard``."""
    items: PasteboardItems = []
    for item in pasteboard.pasteboardItems() or []:
        entry = [
            (str(pb_type), data)
            for pb_type in (item.types() or [])
            if (data := item.dataForType_(pb_type)) is not None
        ]
        if entry:
            items.append(entry)
    return items


def restore_items(
    pasteboard, items: PasteboardItems, make_item: Callable[[], object]
) -> None:
    """Clear ``pasteboard`` and write ``items`` (a :func:`snapshot_items`
    result) back, one fresh ``make_item()`` per snapshotted item."""
    pasteboard.clearContents()
    restored = []
    for entry in items:
        item = make_item()
        for pb_type, data in entry:
            item.setData_forType_(data, pb_type)
        restored.append(item)
    if restored:
        pasteboard.writeObjects_(restored)


class DarwinPasteboard:
    """AppKit-backed snapshot/restore of the general pasteboard (macOS only).

    Raises ``ImportError`` when pyobjc's Cocoa framework is not installed;
    the caller (``PynputSelectionBackend``) treats that as "degrade to the
    text-only clipboard round-trip", never as a crash.
    """

    def __init__(self) -> None:
        from AppKit import NSPasteboard, NSPasteboardItem

        self._pasteboard = NSPasteboard.generalPasteboard()
        self._item_class = NSPasteboardItem

    def snapshot(self) -> PasteboardItems:
        return snapshot_items(self._pasteboard)

    def restore(self, items: PasteboardItems) -> None:
        restore_items(self._pasteboard, items, lambda: self._item_class.alloc().init())
