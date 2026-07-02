"""Deterministic, testable text rules applied around the LLM polish step.

These rules run locally with no model involved:

- filler removal ("um", "uh", ...)
- backtracking ("send it to John, scratch that, send it to Sarah")
- dictionary enforcement (canonical spellings such as "PostgreSQL")
- snippet expansion (spoken trigger phrases -> stored text)
- dictation commands ("new line", "new paragraph", trailing "press enter")
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


def enforce_dictionary(text: str, terms: Iterable[str]) -> str:
    """Rewrite case-insensitive matches of each term to its canonical form.

    Multi-word terms tolerate flexible whitespace, so ``"jispr   flow"``
    still becomes ``"JiSpr Flow"``. Matches respect word boundaries.
    """
    for term in terms:
        if not term.strip():
            continue
        escaped = re.escape(term.strip()).replace(r"\ ", r"\s+")
        text = re.sub(rf"(?i)(?<!\w){escaped}(?!\w)", term.strip(), text)
    return text


def expand_snippets(text: str, snippets: Mapping[str, str]) -> str:
    """Replace spoken trigger phrases with their stored expansions.

    Triggers match case-insensitively on word boundaries; longer triggers win
    so ``"sig block work"`` is preferred over ``"sig block"``.
    """
    for trigger in sorted(snippets, key=len, reverse=True):
        if not trigger.strip():
            continue
        escaped = re.escape(trigger.strip()).replace(r"\ ", r"\s+")
        expansion = snippets[trigger]
        text = re.sub(rf"(?i)(?<!\w){escaped}(?!\w)", lambda _m, _e=expansion: _e, text)
    return text


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
