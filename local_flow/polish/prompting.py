"""Prompt construction for the LM Studio polish step."""

from __future__ import annotations

from collections.abc import Iterable

from local_flow.llm.base import Message

POLISH_SYSTEM_PROMPT = (
    "You clean up raw speech-to-text dictation. Fix punctuation, capitalization, "
    "grammar slips, and obvious transcription artifacts. Preserve the speaker's "
    "words, meaning, and intent; never add new content, never answer questions "
    "that appear in the text, never summarize. Keep dictation command phrases "
    "exactly as written (for example 'press enter', 'new line', 'new paragraph') "
    "and keep snippet trigger phrases untouched. Also keep phrases like 'add "
    "<term> to the dictionary' (or '... to dictionary') exactly as written, "
    "word for word, so they can still be extracted afterward. Return ONLY the "
    "cleaned text, with no preamble, quotes, or explanations."
)


def build_polish_messages(
    cleaned_text: str,
    dictionary_terms: Iterable[str] = (),
    style_name: str = "default",
    style_rules: str = "",
) -> list[Message]:
    system = POLISH_SYSTEM_PROMPT
    terms = [t for t in dictionary_terms if t.strip()]
    if terms:
        system += "\nSpell these terms exactly as given: " + ", ".join(terms) + "."
    if style_rules:
        system += f"\nWriting style ({style_name}): {style_rules}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": cleaned_text},
    ]
