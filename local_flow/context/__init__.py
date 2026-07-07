"""Frontmost-app awareness: best-effort adapters that never raise.

Also holds the :class:`~local_flow.context.router.ContextRouter`, which
resolves per-utterance style/sink/app_id overrides from the frontmost app.
"""

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
    "FrontmostAppProvider",
    "MacFrontmostApp",
    "MockFrontmostApp",
    "ResolvedContext",
    "WindowsFrontmostApp",
    "X11FrontmostApp",
    "create_frontmost_provider",
]
