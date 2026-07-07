"""Mine dictation history for candidate dictionary terms.

``local-flow learn`` (see ``local_flow.app``) surfaces words the speaker uses
repeatedly that are not yet in the personal dictionary: proper nouns,
acronyms, CamelCase product names, and dotted identifiers such as
``config.py``. Everything here is pure/offline — it only reads already
recorded :class:`~local_flow.history.store.HistoryRecord` objects.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from local_flow.history.store import HistoryRecord
from local_flow.personalization.store import fold_term

# Common English words and sentence-starters that would otherwise flood
# suggestions once they're capitalized at the start of a sentence (or, for a
# few of these, even mid-sentence). Compared against the same
# lowercased+apostrophe-folded form used for dictionary dedup.
STOPWORDS: frozenset[str] = frozenset(
    {
        "i", "the", "this", "that", "these", "those", "he", "she", "we", "they",
        "you", "it", "a", "an", "and", "but", "or", "so", "if", "when", "while",
        "where", "what", "who", "which", "why", "how", "then", "there", "here",
        "today", "tomorrow", "yesterday", "now", "also", "just", "please",
        "thanks", "yes", "no", "okay", "well",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "january", "february", "march", "april", "may", "june", "july", "august",
        "september", "october", "november", "december",
    }
)

# A "word" for mining purposes: starts with a letter, then letters/digits,
# optionally continuing through an internal dot or apostrophe followed by more
# letters/digits (so "config.py" and "Iva's" tokenize as single candidates
# without swallowing sentence-ending punctuation or leading quotes).
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[.'’][A-Za-z0-9]+)*")

_SAMPLE_LIMIT = 80


@dataclass(frozen=True)
class Suggestion:
    term: str
    count: int
    sample: str  # one containing sentence fragment (<=80 chars)


def _is_sentence_initial(text: str, start: int) -> bool:
    """True when ``start`` begins a sentence (start of text, or after .!?)."""
    i = start
    while i > 0 and text[i - 1] in " \t\n\r":
        i -= 1
    if i == 0:
        return True
    return text[i - 1] in ".!?"


def _is_camel_case(token: str) -> bool:
    """True for tokens with an internal lower->upper transition (JiSpr, PostgreSQL)."""
    return bool(re.search(r"[a-z][A-Z]", token))


def _is_all_caps(token: str) -> bool:
    letters = [c for c in token if c.isalpha()]
    return len(letters) >= 2 and token.isupper()


def _is_dotted(token: str) -> bool:
    return "." in token


def _matches_heuristic(token: str, sentence_initial: bool) -> bool:
    if _is_camel_case(token):
        return True
    if _is_all_caps(token):
        return True
    if _is_dotted(token):
        return True
    return not sentence_initial and token[0].isupper()


def _fragment(text: str, start: int, end: int, limit: int = _SAMPLE_LIMIT) -> str:
    """A <=``limit``-char window of ``text`` around ``[start:end)``, ellipsized."""
    if len(text) <= limit:
        return text
    budget = max(limit - (end - start), 0)
    left = budget // 2
    window_start = max(0, start - left)
    window_end = min(len(text), window_start + limit)
    window_start = max(0, window_end - limit)

    fragment = text[window_start:window_end]
    if window_start > 0:
        fragment = "…" + fragment[1:]
    if window_end < len(text):
        fragment = fragment[:-1] + "…"
    return fragment


def suggest_terms(
    records: Iterable[HistoryRecord],
    known: Iterable[str],
    min_count: int = 3,
    limit: int = 20,
) -> list[Suggestion]:
    """Rank candidate dictionary terms mined from ``records``' ``final`` text.

    ``known`` (existing dictionary terms) is excluded case-insensitively and
    apostrophe-fold-insensitively (so "Iva" in the dictionary also excludes a
    mined "Iva's"). Counting is case-insensitive but each suggestion reports
    the most frequent original casing seen. Results are sorted by count
    descending.
    """
    known_folded = {fold_term(term) for term in known}
    variant_counts: dict[str, dict[str, int]] = {}
    samples: dict[str, str] = {}
    order: list[str] = []

    for record in records:
        text = record.final
        if not text:
            continue
        for match in _TOKEN_RE.finditer(text):
            token = match.group()
            sentence_initial = _is_sentence_initial(text, match.start())
            if not _matches_heuristic(token, sentence_initial):
                continue
            folded = fold_term(token)
            if not folded or folded in known_folded or folded in STOPWORDS:
                continue
            if folded not in variant_counts:
                variant_counts[folded] = {}
                order.append(folded)
                samples[folded] = _fragment(text, match.start(), match.end())
            variant_counts[folded][token] = variant_counts[folded].get(token, 0) + 1

    suggestions: list[Suggestion] = []
    for folded in order:
        variants = variant_counts[folded]
        total = sum(variants.values())
        if total < min_count:
            continue
        best_casing = max(variants.items(), key=lambda kv: kv[1])[0]
        suggestions.append(Suggestion(term=best_casing, count=total, sample=samples[folded]))

    suggestions.sort(key=lambda s: s.count, reverse=True)
    return suggestions[:limit]
