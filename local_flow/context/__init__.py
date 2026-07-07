"""Frontmost-app and focused-field awareness: best-effort adapters that never raise.

Also holds the :class:`~local_flow.context.router.ContextRouter`, which
resolves per-utterance style/sink/app_id overrides from the frontmost app,
and the E10 field-text adapters (:mod:`local_flow.context.field_text`) that
let polish see the focused field's existing text.
"""

from local_flow.context.field_text import (
    FieldContext,
    FieldTextProvider,
    MacAXFieldText,
    MockFieldText,
    NullFieldText,
    WindowsUIAFieldText,
    create_field_text_provider,
)
from local_flow.context.frontmost import (
    AppInfo,
    FrontmostAppProvider,
    MacFrontmostApp,
    MockFrontmostApp,
    WindowsFrontmostApp,
    X11FrontmostApp,
    create_frontmost_provider,
)
from local_flow.context.router import ContextRouter, ResolvedContext

__all__ = [
    "AppInfo",
    "ContextRouter",
    "FieldContext",
    "FieldTextProvider",
    "FrontmostAppProvider",
    "MacAXFieldText",
    "MacFrontmostApp",
    "MockFieldText",
    "MockFrontmostApp",
    "NullFieldText",
    "ResolvedContext",
    "WindowsFrontmostApp",
    "WindowsUIAFieldText",
    "X11FrontmostApp",
    "create_field_text_provider",
    "create_frontmost_provider",
]
