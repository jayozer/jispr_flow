"""Frontmost-app awareness: best-effort adapters that never raise."""

from local_flow.context.frontmost import (
    AppInfo,
    FrontmostAppProvider,
    MacFrontmostApp,
    MockFrontmostApp,
    WindowsFrontmostApp,
    X11FrontmostApp,
    create_frontmost_provider,
)

__all__ = [
    "AppInfo",
    "FrontmostAppProvider",
    "MacFrontmostApp",
    "MockFrontmostApp",
    "WindowsFrontmostApp",
    "X11FrontmostApp",
    "create_frontmost_provider",
]
