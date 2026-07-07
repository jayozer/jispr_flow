"""Deterministic, testable text rules applied around the LLM polish step.

These rules run locally with no model involved:

- filler removal ("um", "uh", ...)
- backtracking ("send it to John, scratch that, send it to Sarah")
- dictionary enforcement (canonical spellings such as "PostgreSQL")
- snippet expansion (spoken trigger phrases -> stored text)
- dictation commands ("new line", "new paragraph", trailing "press enter")
- spoken dictionary additions ("add JiSpr to the dictionary")
- spoken code syntax ("camel case order total" -> "orderTotal")
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

FILLER_WORDS: frozenset[str] = frozenset(
    {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "mhm"}
)

# When one of these phrases appears as (or at the start of) a comma/period
# separated segment, the *previous* segment is discarded: the speaker is
# correcting themselves and the words after the marker replace it.
BACKTRACK_MARKERS: tuple[str, ...] = (
    "scratch that",
    "strike that",
    "no wait",
    "wait no",
    "actually no",
    "i mean",
)

_KEY_COMMANDS: dict[str, str] = {
    "press enter": "enter",
    "hit enter": "enter",
    "press tab": "tab",
    "hit tab": "tab",
}


def normalize_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([,.;!?])", r"\1", text)
    text = re.sub(r"([,.;!?])(?:\s*\1)+", r"\1", text)  # collapse ",," / ".."
    text = re.sub(r"^\s*[,.;]+\s*", "", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def remove_fillers(text: str, fillers: Iterable[str] = FILLER_WORDS) -> str:
    """Remove standalone filler words, keeping surrounding punctuation sane."""
    pattern = "|".join(sorted((re.escape(f) for f in fillers), key=len, reverse=True))
    if not pattern:
        return text
    text = re.sub(rf"(?i)(?<![\w'-])(?:{pattern})(?![\w'-])[,.]?", "", text)
    return normalize_whitespace(text)


def apply_backtracking(text: str, markers: Iterable[str] = BACKTRACK_MARKERS) -> str:
    """Apply self-corrections: drop the segment before a backtrack marker.

    Segments are delimited by commas and sentence punctuation. In
    ``"email John, scratch that, email Sarah"`` the marker segment
    ``scratch that`` removes ``email John`` and itself, leaving
    ``email Sarah``. Words after the marker inside the same segment are kept
    (``"..., no wait email Sarah"``).
    """
    marker_list = sorted(markers, key=len, reverse=True)
    parts = re.split(r"([,.;!?])", text)
    # parts alternates [segment, delimiter, segment, ...]; pair them up.
    segments: list[tuple[str, str]] = []
    for i in range(0, len(parts), 2):
        seg = parts[i]
        delim = parts[i + 1] if i + 1 < len(parts) else ""
        segments.append((seg, delim))

    result: list[tuple[str, str]] = []
    for seg, delim in segments:
        stripped = seg.strip()
        lowered = stripped.lower()
        matched = next(
            (m for m in marker_list if lowered == m or lowered.startswith(m + " ")),
            None,
        )
        if matched is None:
            if stripped or delim:
                result.append((seg, delim))
            continue
        if result:
            result.pop()
        remainder = stripped[len(matched):].strip(" ,")
        if remainder:
            result.append((" " + remainder, delim))
        # An empty remainder drops the marker segment and its delimiter too;
        # the replacement text arrives in the next segment.

    rebuilt = "".join(seg + delim for seg, delim in result)
    return normalize_whitespace(rebuilt)


def clean_transcript(text: str, fillers: Iterable[str] = FILLER_WORDS) -> str:
    """Rule-based cleanup that runs before (or instead of) the LLM polish."""
    return remove_fillers(apply_backtracking(text), fillers)


def enforce_dictionary_detailed(text: str, terms: Iterable[str]) -> tuple[str, dict[str, int]]:
    """Rewrite case-insensitive matches of each term to its canonical form.

    Multi-word terms tolerate flexible whitespace, so ``"jispr   flow"``
    still becomes ``"JiSpr Flow"``. Matches respect word boundaries.

    Returns ``(text, counts)`` where ``counts`` maps each term to the number
    of substitutions performed for it; terms with zero matches are omitted.
    """
    counts: dict[str, int] = {}
    for term in terms:
        stripped = term.strip()
        if not stripped:
            continue
        escaped = re.escape(stripped).replace(r"\ ", r"\s+")
        text, count = re.subn(rf"(?i)(?<!\w){escaped}(?!\w)", stripped, text)
        if count:
            counts[stripped] = counts.get(stripped, 0) + count
    return text, counts


def enforce_dictionary(text: str, terms: Iterable[str]) -> tuple[str, int]:
    """Rewrite case-insensitive matches of each term to its canonical form.

    Multi-word terms tolerate flexible whitespace, so ``"jispr   flow"``
    still becomes ``"JiSpr Flow"``. Matches respect word boundaries.

    Returns ``(text, count)`` where ``count`` is the number of substitutions
    performed across all terms. Thin wrapper around
    :func:`enforce_dictionary_detailed` for callers that only need the total.
    """
    text, counts = enforce_dictionary_detailed(text, terms)
    return text, sum(counts.values())


def expand_snippets(text: str, snippets: Mapping[str, str]) -> tuple[str, int]:
    """Replace spoken trigger phrases with their stored expansions.

    Triggers match case-insensitively on word boundaries; longer triggers win
    so ``"sig block work"`` is preferred over ``"sig block"``.

    Returns ``(text, count)`` where ``count`` is the number of substitutions
    performed across all triggers.
    """
    total = 0
    for trigger in sorted(snippets, key=len, reverse=True):
        if not trigger.strip():
            continue
        escaped = re.escape(trigger.strip()).replace(r"\ ", r"\s+")
        expansion = snippets[trigger]
        text, count = re.subn(rf"(?i)(?<!\w){escaped}(?!\w)", lambda _m, _e=expansion: _e, text)
        total += count
    return text, total


def apply_dictation_commands(text: str) -> tuple[str, list[str]]:
    """Convert spoken formatting commands; return ``(text, trailing_key_actions)``.

    - ``"new line"`` / ``"newline"`` -> ``\\n``
    - ``"new paragraph"`` -> ``\\n\\n``
    - trailing ``"press enter"`` / ``"hit enter"`` -> an ``enter`` key action
      for the text sink to perform after inserting the text
    - a mid-text ``"press enter"`` becomes a plain newline
    """
    actions: list[str] = []
    key_pattern = "|".join(re.escape(k).replace(r"\ ", r"\s+") for k in _KEY_COMMANDS)

    # Peel key commands off the end (possibly several), outermost last. The
    # prefix eats a separating comma/space but leaves sentence periods alone.
    trailing = re.compile(rf"(?i)[,\s]*\b({key_pattern})\b[\s,.!?]*$")
    while True:
        match = trailing.search(text)
        if not match:
            break
        spoken = re.sub(r"\s+", " ", match.group(1).lower())
        actions.insert(0, _KEY_COMMANDS[spoken])
        text = text[: match.start()]

    # Remaining key commands mid-text become newlines.
    text = re.sub(rf"(?i)[,.]?\s*\b(?:{key_pattern})\b[,.]?\s*", "\n", text)
    text = re.sub(r"(?i)[,.]?\s*\bnew\s+paragraph\b[,.]?\s*", "\n\n", text)
    text = re.sub(r"(?i)[,.]?\s*\b(?:new\s+line|newline)\b[,.]?\s*", "\n", text)
    return normalize_whitespace(text), actions


# "add <1-4 words> to [the] dictionary" — the term is captured greedily up to
# the trailing "to (the) dictionary" so multi-word terms ("Kubernetes
# cluster") are captured whole rather than just their last word.
_ADD_TO_DICTIONARY_RE = re.compile(
    r"(?i)\badd\s+((?:\S+\s+){0,3}\S+)\s+to\s+(?:the\s+)?dictionary\b[.,!?]*"
)


def extract_dictionary_additions(text: str) -> tuple[str, list[str]]:
    """Pull spoken "add X to [the] dictionary" phrases out of ``text``.

    Returns ``(text, terms)``: ``terms`` lists each captured term (trimmed)
    in the order spoken, and the matched phrases are removed from ``text``
    with whitespace repaired via :func:`normalize_whitespace`. Multiple
    occurrences in the same utterance are all extracted. Pure rules — this
    runs whether or not LM Studio is reachable.
    """
    terms: list[str] = []

    def _capture(match: re.Match[str]) -> str:
        terms.append(match.group(1).strip())
        return " "

    text = _ADD_TO_DICTIONARY_RE.sub(_capture, text)
    return normalize_whitespace(text), terms


# "camel case <1-4 words>" / "snake case <1-4 words>" / "all caps <1-4 words>":
# the trigger is followed by up to four letters/digits-only words (greedy --
# always claims the maximum available run up to four, it does not try to
# guess where a "natural" phrase boundary is). This is a simple deterministic
# rule, not a language model, so the same trigger words used as ordinary
# language ("I like snake case better") will also convert -- a known,
# accepted false-positive risk for this MVP feature (see README).
_CODE_WORD = r"[A-Za-z0-9]+"
_SPOKEN_CODE_RE = re.compile(
    rf"(?i)\b(camel case|snake case|all caps)\s+((?:{_CODE_WORD}\s+){{0,3}}{_CODE_WORD})"
)


def _to_camel_case(words: list[str]) -> str:
    if not words:
        return ""
    return words[0].lower() + "".join(w.capitalize() for w in words[1:])


def _to_snake_case(words: list[str]) -> str:
    return "_".join(w.lower() for w in words)


def _to_all_caps(words: list[str]) -> str:
    return " ".join(w.upper() for w in words)


_SPOKEN_CODE_TRANSFORMS = {
    "camel case": _to_camel_case,
    "snake case": _to_snake_case,
    "all caps": _to_all_caps,
}


def apply_spoken_code_syntax(text: str) -> tuple[str, int]:
    """Convert spoken code-syntax phrases into literal code tokens.

    ``"camel case order total"`` -> ``"orderTotal"``; ``"snake case user
    id"`` -> ``"user_id"``; ``"all caps api key"`` -> ``"API KEY"``. Each
    trigger phrase (case-insensitive) is followed by 1-4 words made up of
    letters/digits only. Multiple occurrences in the same text are all
    converted.

    Returns ``(text, count)`` where ``count`` is the number of phrases
    converted.
    """
    count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        transform = _SPOKEN_CODE_TRANSFORMS[match.group(1).lower()]
        count += 1
        return transform(match.group(2).split())

    text = _SPOKEN_CODE_RE.sub(_replace, text)
    return normalize_whitespace(text), count
