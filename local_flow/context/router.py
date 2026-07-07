"""Resolve per-utterance style/sink/app_id from the frontmost app.

Consulted once per utterance (when recording stops), not per insert, so a
single ``resolve()`` call determines the style override, insertion sink, and
history ``app`` value for that dictation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from local_flow.context.frontmost import FrontmostAppProvider
from local_flow.insertion.base import TextSink
from local_flow.personalization.store import AppRule, match_app_rule


@dataclass(frozen=True)
class ResolvedContext:
    """What ``DictationPipeline.process_transcript`` should use for this utterance."""

    app_id: str = ""
    style: str | None = None  # None -> polisher default
    sink: TextSink | None = None  # None -> pipeline default sink


class ContextRouter:
    """Maps the frontmost app to a style/sink override via ``AppRule`` matching."""

    def __init__(
        self,
        provider: FrontmostAppProvider,
        rules: dict[str, AppRule],
        sinks_by_method: Mapping[str, TextSink],
    ) -> None:
        self.provider = provider
        self.rules = rules
        self.sinks_by_method = sinks_by_method

    def resolve(self) -> ResolvedContext:
        info = self.provider.current()
        rule = match_app_rule(self.rules, info.app_id, info.title)
        if rule is None:
            return ResolvedContext(app_id=info.app_id)
        style = rule.style or None
        sink = self.sinks_by_method.get(rule.insert) if rule.insert else None
        return ResolvedContext(app_id=info.app_id, style=style, sink=sink)
