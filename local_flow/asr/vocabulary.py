"""Shared ASR vocabulary-prompt construction."""

from __future__ import annotations

from collections.abc import Sequence

INITIAL_PROMPT_MAX_CHARS = 1000
_INITIAL_PROMPT_PREFIX = "Important vocabulary: "


def build_initial_prompt(
    terms: Sequence[str], max_chars: int = INITIAL_PROMPT_MAX_CHARS
) -> str:
    """Build a deterministic, bounded Whisper vocabulary prompt.

    ``PersonalizationStore.dictionary_terms`` already returns the most useful
    terms first (starred, then frequently used), so the character cap keeps
    that order. Blank/duplicate terms are removed and embedded whitespace is
    collapsed so hand-edited dictionary data cannot create a malformed prompt.
    An overlong term is skipped rather than preventing later short terms from
    fitting.
    """
    if max_chars <= len(_INITIAL_PROMPT_PREFIX):
        return ""

    accepted: list[str] = []
    seen: set[str] = set()
    for raw in terms:
        term = " ".join(str(raw).split())
        folded = term.casefold()
        if not term or folded in seen:
            continue
        candidate_terms = [*accepted, term]
        candidate = _INITIAL_PROMPT_PREFIX + ", ".join(candidate_terms)
        if len(candidate) > max_chars:
            continue
        accepted.append(term)
        seen.add(folded)

    return _INITIAL_PROMPT_PREFIX + ", ".join(accepted) if accepted else ""
