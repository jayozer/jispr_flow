"""Combine rule-based cleanup with the LM Studio polish pass."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from local_flow.context.field_text import FieldContext
from local_flow.errors import LMStudioError, LMStudioResponseError
from local_flow.llm.base import ChatClient
from local_flow.personalization.store import DEFAULT_STYLES, PersonalizationStore
from local_flow.polish.prompting import build_polish_messages
from local_flow.polish.rules import clean_transcript, enforce_dictionary_detailed, remove_fillers
from local_flow.polish.s1_mini import has_protected_speech, split_transcript

_ASSISTANT_PREFIX = re.compile(
    r"^\s*(?:assistant|ai)\s*:|^\s*(?:sure|certainly|of course|here['’]s)\b",
    re.IGNORECASE,
)
_GRATITUDE = re.compile(r"\b(?:thank(?:s| you)?|much appreciated)\b", re.IGNORECASE)
_GRATITUDE_REPLY = re.compile(
    r"\b(?:you(?:['’]re| are) welcome|my pleasure|glad to help|anytime)\b",
    re.IGNORECASE,
)


def _unsafe_polish_reason(source: str, candidate: str) -> str | None:
    """Explain why an LLM completion is not a faithful transcript rewrite.

    The polisher is not a chatbot. Model-template tokens, assistant preambles,
    and conversational replies are strong evidence that the local model
    answered the transcript instead of editing it. The check is deliberately
    narrow: legitimate dictated phrases such as "You're welcome" remain valid
    when they were present in ``source``.
    """
    if "<|" in candidate or "|>" in candidate:
        return "response contained model control tokens"
    if _ASSISTANT_PREFIX.search(candidate) and not _ASSISTANT_PREFIX.search(source):
        return "response looked like an assistant reply"
    if (
        _GRATITUDE.search(source)
        and not _GRATITUDE_REPLY.search(source)
        and _GRATITUDE_REPLY.search(candidate)
    ):
        return "response answered the dictated text instead of polishing it"
    return None


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
        level: str = "medium",
        fallback_to_rules: bool = True,
        system_prompt: str = "",
        language: str | None = None,
    ) -> None:
        self.chat_client = chat_client
        self.store = store
        self._style = style
        self._level = level
        self.fallback_to_rules = fallback_to_rules
        self.system_prompt = system_prompt
        self.language = language

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

    @property
    def level(self) -> str:
        """Cleanup level used by :meth:`polish` (none|light|medium|high; see
        ``local_flow.config.Config.cleanup_level``).

        Settable so a caller can change the active cleanup level for future
        utterances without rebuilding the pipeline, mirroring ``style`` above.
        """
        return self._level

    @level.setter
    def level(self, value: str) -> None:
        self._level = value

    def polish(
        self,
        rough: str,
        style: str | None = None,
        field_context: FieldContext | None = None,
        language: str | None = None,
    ) -> PolishResult:
        """Rules first, then an LLM polish pass using ``style``.

        ``style`` overrides the constructor default for this one call
        (``None`` keeps using ``self.style``); this is how per-app style
        overrides from :class:`local_flow.context.router.ContextRouter` reach
        the polisher without changing any other call site.

        ``field_context`` (E10, see ``local_flow.context.field_text``) is the
        focused field's existing text, best-effort and resolved once per
        utterance by :class:`local_flow.pipeline.DictationPipeline`; ``None``
        (the default) adds nothing to the prompt, so callers that never pass
        it are unaffected. Forwarded straight to
        :func:`~local_flow.polish.prompting.build_polish_messages`, which
        only appends a continuation block when it is non-empty.

        At ``level == "none"`` this returns ``rough`` untouched in both
        ``cleaned`` and ``polished`` -- no rule-based cleanup runs and
        ``self.chat_client`` is never called. Dictionary/snippet/dictation
        command handling still happens downstream in
        :class:`local_flow.pipeline.DictationPipeline` (that's
        personalization, not cleanup).
        """
        if self._level == "none":
            return PolishResult(rough=rough, cleaned=rough, polished=rough)

        cleaned = clean_transcript(rough)
        result = PolishResult(rough=rough, cleaned=cleaned, polished=cleaned)
        # Nothing remains to polish. In particular, do not resolve an
        # auto-selected model over HTTP for filler-only input.
        if not cleaned or self.chat_client is None:
            return result

        try:
            if self.chat_client.is_transcript_normalizer:
                return self._normalize(
                    rough, result, style, language if language is not None else self.language
                )
        except LMStudioError as exc:
            if not self.fallback_to_rules:
                raise
            result.warnings.append(f"LM Studio polish skipped: {exc.message}")
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
            level=self._level,
            field_context=field_context,
            additional_system_prompt=self.system_prompt,
        )
        try:
            polished = self.chat_client.chat(messages)
        except LMStudioError as exc:
            if not self.fallback_to_rules:
                raise
            result.warnings.append(f"LM Studio polish skipped: {exc.message}")
            return result
        if polished:
            reason = _unsafe_polish_reason(cleaned, polished)
            if reason is not None:
                result.warnings.append(f"LM Studio polish rejected: {reason}")
                return result
            result.polished = polished
            result.used_llm = True
        return result

    def _normalize(
        self,
        rough: str,
        result: PolishResult,
        style: str | None,
        language: str | None,
    ) -> PolishResult:
        """Keep the specialized model independent of general-purpose prompts.

        Feed raw ASR text so rule-based backtracking cannot discard the context
        needed to resolve a self-correction. Rules remain the failure fallback.
        Field context and arbitrary instructions are never put in S1's prompt.
        """
        assert self.chat_client is not None
        if language and language.lower() not in ("en", "english", "auto"):
            raise LMStudioResponseError("S1-mini supports English only; using rules.")
        requested_style = style if style is not None else self.style
        style_name, style_rules = self.store.style_rules(requested_style)
        if (
            requested_style and style_name != requested_style
            or style_rules != DEFAULT_STYLES.get(style_name)
            or self.system_prompt.strip()
        ):
            raise LMStudioResponseError(
                "S1-mini cannot apply custom polish instructions or styles; using rules. "
                "Select Gemma or another general-purpose model to use those instructions."
            )
        snippets = self.store.snippets()
        if has_protected_speech(rough, snippets):
            raise LMStudioResponseError(
                "Using rules to preserve spoken commands and snippet triggers with S1-mini."
            )
        if self.level == "high":
            result.warnings.append(
                "S1-mini applies standard cleanup; high-level rewriting is unused."
            )
        chunks = split_transcript(rough)
        if style_name == "email" and len(chunks) > 1:
            raise LMStudioResponseError("S1-mini email is too long for one pass; using rules.")
        outputs: list[str] = []
        terms = self.store.dictionary_terms()
        for chunk in chunks:
            candidate = self.chat_client.normalize_transcript(chunk, style=style_name)
            # Empty is valid for filler/noise, never proof that a meaningful
            # utterance should be discarded (including an accidental blank response).
            if not candidate and re.search(r"\w", remove_fillers(chunk)):
                raise LMStudioResponseError("S1-mini returned no text for speech; using rules.")
            reason = _unsafe_polish_reason(chunk, candidate)
            if reason or has_protected_speech(candidate, snippets):
                reason = reason or "introduced a spoken command or snippet"
                raise LMStudioResponseError(
                    f"S1-mini cleanup rejected: {reason}."
                )
            _, before = enforce_dictionary_detailed(chunk, terms)
            _, after = enforce_dictionary_detailed(candidate, terms)
            if any(after.get(term, 0) < count for term, count in before.items()):
                raise LMStudioResponseError("S1-mini changed a dictionary term; using rules.")
            if candidate:
                outputs.append(candidate)
        polished = "\n\n".join(outputs)
        if has_protected_speech(polished, snippets):
            raise LMStudioResponseError("S1-mini introduced a command across chunks; using rules.")
        result.polished = polished
        result.used_llm = True
        return result
