"""Combine rule-based cleanup with the LM Studio polish pass."""

from __future__ import annotations

from dataclasses import dataclass, field

from local_flow.errors import LMStudioError
from local_flow.llm.base import ChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.polish.prompting import build_polish_messages
from local_flow.polish.rules import clean_transcript


@dataclass
class PolishResult:
    rough: str
    cleaned: str
    polished: str
    used_llm: bool = False
    warnings: list[str] = field(default_factory=list)


class TranscriptPolisher:
    """Rules first, then an optional LLM pass.

    With ``chat_client=None`` (or when LM Studio is unreachable and
    ``fallback_to_rules`` is on), the rule-cleaned text is returned so
    dictation keeps working offline.
    """

    def __init__(
        self,
        chat_client: ChatClient | None,
        store: PersonalizationStore,
        style: str = "default",
        fallback_to_rules: bool = True,
    ) -> None:
        self.chat_client = chat_client
        self.store = store
        self._style = style
        self.fallback_to_rules = fallback_to_rules

    @property
    def style(self) -> str:
        """Default style name used by :meth:`polish` when called without an
        explicit ``style=`` override.

        Settable so a caller (e.g. the tray app's Style submenu) can change
        the active style for future utterances without rebuilding the
        pipeline.
        """
        return self._style

    @style.setter
    def style(self, value: str) -> None:
        self._style = value

    def polish(self, rough: str, style: str | None = None) -> PolishResult:
        """Rules first, then an LLM polish pass using ``style``.

        ``style`` overrides the constructor default for this one call
        (``None`` keeps using ``self.style``); this is how per-app style
        overrides from :class:`local_flow.context.router.ContextRouter` reach
        the polisher without changing any other call site.
        """
        cleaned = clean_transcript(rough)
        result = PolishResult(rough=rough, cleaned=cleaned, polished=cleaned)
        if not cleaned or self.chat_client is None:
            return result

        requested_style = style if style is not None else self.style
        style_name, style_rules = self.store.style_rules(requested_style)
        if requested_style and style_name != requested_style:
            result.warnings.append(
                f"style {requested_style!r} not found; using {style_name!r}"
            )
        messages = build_polish_messages(
            cleaned,
            dictionary_terms=self.store.dictionary_terms(),
            style_name=style_name,
            style_rules=style_rules,
        )
        try:
            polished = self.chat_client.chat(messages)
        except LMStudioError as exc:
            if not self.fallback_to_rules:
                raise
            result.warnings.append(f"LM Studio polish skipped: {exc.message}")
            return result
        if polished:
            result.polished = polished
            result.used_llm = True
        return result
