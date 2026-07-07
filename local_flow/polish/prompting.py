"""Prompt construction for the LM Studio polish step.

Cleanup levels (see ``local_flow.config.Config.cleanup_level`` and
``local_flow.polish.polisher.TranscriptPolisher``):

- ``none``:   no LLM call at all; ``TranscriptPolisher.polish`` short-circuits
              before any prompt is built, so this module has no ``none``
              prompt.
- ``light``:  fillers/grammar only, no rephrasing.
- ``medium``: today's default polish behavior. ``POLISH_SYSTEM_PROMPT`` below
              is pinned byte-identical to the original (pre-levels) prompt.
- ``high``:   rewrite for concision while preserving meaning.

Across light/medium/high the "protections" (dictation command phrases,
snippet triggers, spoken "add ... to the dictionary") and the list-formatting
instruction are shared verbatim; only the cleanup instruction itself varies
per level.

E10 context awareness (see ``local_flow.context.field_text``): when the
caller has a non-empty :class:`~local_flow.context.field_text.FieldContext`
(the focused field's existing text, best-effort), an extra block is appended
*after* everything else so polish continues the sentence, matches its tone,
and reuses name spellings already present, instead of re-greeting or
clashing with what's already there. Absent/empty context appends nothing --
the resulting prompt is then byte-identical to the pre-E10 prompt (pinned by
``tests/test_field_context.py`` alongside ``tests/test_cleanup_levels.py``'s
existing pins).
"""

from __future__ import annotations

from collections.abc import Iterable

from local_flow.context.field_text import MAX_BEFORE_CURSOR, FieldContext
from local_flow.llm.base import Message

# Shared across every LLM level: never let the polish pass eat phrases that
# downstream rule-based processing still needs to find.
_PROTECTIONS = (
    "Keep dictation command phrases exactly as written (for example 'press "
    "enter', 'new line', 'new paragraph') and keep snippet trigger phrases "
    "untouched. Also keep phrases like 'add <term> to the dictionary' (or "
    "'... to dictionary') exactly as written, word for word, so they can "
    "still be extracted afterward."
)

_RETURN_ONLY = "Return ONLY the cleaned text, with no preamble, quotes, or explanations."

# Shared across every LLM level: spoken enumerations should come back as an
# actual list rather than a run-on sentence.
_LIST_FORMATTING = (
    "When the speaker enumerates items aloud (spoken sequences like 'first "
    "..., second ..., third ...', or a run of comma-separated items), format "
    "them as a proper numbered or bulleted list instead of a run-on sentence."
)

# Shared across every LLM level: spoken code-syntax phrases (see
# `local_flow.polish.rules.apply_spoken_code_syntax`) must survive polish
# untouched so the rules stage can still convert them afterward. Appended
# separately from `_PROTECTIONS` (rather than folded into it) because
# `_PROTECTIONS` feeds `POLISH_SYSTEM_PROMPT`, which is pinned byte-identical
# to the pre-cleanup-levels prompt (see
# `tests/test_cleanup_levels.py::TestMediumPromptPinned`).
_CODE_SYNTAX_PROTECTION = (
    "Also keep spoken code-syntax phrases like 'camel case ...', 'snake case "
    "...', and 'all caps ...' exactly as spoken, word for word, so they can "
    "still be converted afterward."
)

# The cleanup instruction proper -- this is the only segment that differs
# between levels; everything else above is shared.
_CLEANUP_LIGHT = (
    "You clean up raw speech-to-text dictation. Fix grammar and remove "
    "filler words; do not rephrase, reword, or restructure sentences beyond "
    "that minimal cleanup."
)

_CLEANUP_MEDIUM = (
    "You clean up raw speech-to-text dictation. Fix punctuation, "
    "capitalization, grammar slips, and obvious transcription artifacts. "
    "Preserve the speaker's words, meaning, and intent; never add new "
    "content, never answer questions that appear in the text, never "
    "summarize."
)

_CLEANUP_HIGH = (
    "You clean up raw speech-to-text dictation. Rewrite it for concision "
    "and polish while preserving the speaker's meaning and intent; trim "
    "redundancy and filler without dropping any facts."
)

# `POLISH_SYSTEM_PROMPT` is pinned byte-identical to the prompt that existed
# before cleanup levels: it must stay exactly `_CLEANUP_MEDIUM` + `_PROTECTIONS`
# + `_RETURN_ONLY`, joined by single spaces, with nothing else mixed in (see
# `tests/test_cleanup_levels.py::TestMediumPromptPinned`).
POLISH_SYSTEM_PROMPT = f"{_CLEANUP_MEDIUM} {_PROTECTIONS} {_RETURN_ONLY}"

_CLEANUP_BY_LEVEL: dict[str, str] = {
    "light": _CLEANUP_LIGHT,
    "medium": _CLEANUP_MEDIUM,
    "high": _CLEANUP_HIGH,
}

# E10 context-continuation block: appended verbatim (with `before_cursor`'s
# tail interpolated) after every other segment, only when there is actually
# something to continue from. Exact wording is pinned by
# `tests/test_field_context.py`.
_FIELD_CONTEXT_TEMPLATE = (
    "The user is continuing existing text that ends with: {before_cursor}. "
    "Continue naturally from it: do not repeat it, match its tone and "
    "formatting, and reuse the exact spellings of any names or terms "
    "appearing in it. Return only the new text."
)


def _field_context_block(field_context: FieldContext | None) -> str:
    """Build the optional E10 context-continuation block, or ``""``.

    ``""`` (no block) whenever ``field_context`` is ``None`` or both of its
    fields are empty -- that is what keeps a caller that never passes
    ``field_context`` (or passes an all-empty one, e.g. because the
    accessibility read failed) byte-identical to before E10 existed.

    ``before_cursor`` is defensively re-capped at ``MAX_BEFORE_CURSOR``
    characters here too, even though every ``FieldTextProvider`` is expected
    to already cap it (see ``local_flow.context.field_text``) -- so the
    prompt can never balloon regardless of what a provider hands back.
    """
    if field_context is None:
        return ""
    if not field_context.before_cursor and not field_context.selected:
        return ""
    tail = field_context.before_cursor[-MAX_BEFORE_CURSOR:]
    return _FIELD_CONTEXT_TEMPLATE.format(before_cursor=tail)


def _system_prompt_for_level(level: str) -> str:
    """Assemble the base system prompt for one LLM cleanup level.

    Unknown levels fall back to ``medium`` (defensive; ``Config`` validation
    already restricts ``cleanup_level`` to a known set, and ``none`` never
    reaches here because :meth:`TranscriptPolisher.polish` short-circuits
    before calling this).
    """
    cleanup = _CLEANUP_BY_LEVEL.get(level, _CLEANUP_MEDIUM)
    # For `medium` this starts with `POLISH_SYSTEM_PROMPT` verbatim, followed
    # by `_LIST_FORMATTING` and `_CODE_SYNTAX_PROTECTION`.
    return (
        f"{cleanup} {_PROTECTIONS} {_RETURN_ONLY} {_LIST_FORMATTING} "
        f"{_CODE_SYNTAX_PROTECTION}"
    )


def build_polish_messages(
    cleaned_text: str,
    dictionary_terms: Iterable[str] = (),
    style_name: str = "default",
    style_rules: str = "",
    level: str = "medium",
    field_context: FieldContext | None = None,
) -> list[Message]:
    system = _system_prompt_for_level(level)
    terms = [t for t in dictionary_terms if t.strip()]
    if terms:
        system += "\nSpell these terms exactly as given: " + ", ".join(terms) + "."
    if style_rules:
        system += f"\nWriting style ({style_name}): {style_rules}"
    context_block = _field_context_block(field_context)
    if context_block:
        system += "\n" + context_block
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": cleaned_text},
    ]
