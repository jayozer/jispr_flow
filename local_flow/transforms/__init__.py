"""Text transforms: named AI rewrites applied to text or the OS selection."""

from local_flow.transforms.registry import apply_transform, build_transform_messages
from local_flow.transforms.selection import (
    MockSelectionBackend,
    PynputSelectionBackend,
    SelectionBackend,
    SelectionCapture,
)

__all__ = [
    "MockSelectionBackend",
    "PynputSelectionBackend",
    "SelectionBackend",
    "SelectionCapture",
    "apply_transform",
    "build_transform_messages",
]
