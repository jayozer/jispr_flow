"""S1-mini by Superwhisper: its fixed v1 protocol and conservative input bounds.

Protocol: https://huggingface.co/superwhisper/s1-mini-GGUF
Use raw completions so LM Studio cannot enable Qwen3 thinking or replace
the required template. No tokenizer, weights, or extra runtime is needed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from local_flow.errors import LMStudioResponseError
from local_flow.polish.rules import (
    apply_dictation_commands,
    apply_spoken_code_syntax,
    expand_snippets,
    extract_dictionary_additions,
    normalize_whitespace,
)

SYSTEM_PROMPT = (
    "You are a text normalizer for speech-to-text transcripts. The input begins "
    "with a control line specifying the styling, structure, and context settings; "
    "clean the transcript to match those settings and output only the cleaned text."
)

# UTF-8 bytes conservatively bound byte-level tokenizer input without adding
# a tokenizer dependency. Leave room for the fixed prompt below ~1,000 tokens.
MAX_CHUNK_BYTES = 800
MAX_CHUNKS = 16
STYLE_CONTROLS = {
    "default": ("semi-formal", "lists", "general"),
    "professional": ("formal", "lists", "general"),
    "casual": ("semi-casual", "lists", "general"),
    "chat": ("semi-casual", "prose", "general"),
    "email": ("formal", "prose", "email"),
}


def is_s1_mini(model: str) -> bool:
    """Recognize standard LM Studio ids, HF paths, and GGUF filenames."""
    return re.search(r"(?:^|[/\\])s1-mini(?:$|[-_.:@/])", model, re.IGNORECASE) is not None


def split_transcript(text: str) -> list[str]:
    """Split at sentence/paragraph boundaries; never truncate or split a sentence."""
    chunks: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text.strip()):
        if not sentence:
            continue
        if len(sentence.encode("utf-8")) > MAX_CHUNK_BYTES:
            raise LMStudioResponseError(
                "S1-mini input contains a sentence that is too long; using rules."
            )
        joined = f"{chunks[-1]} {sentence}" if chunks else sentence
        if chunks and len(joined.encode("utf-8")) <= MAX_CHUNK_BYTES:
            chunks[-1] = joined
        else:
            chunks.append(sentence)
    if len(chunks) > MAX_CHUNKS:
        raise LMStudioResponseError("S1-mini input is too long; using rules.")
    return chunks


def build_prompt(transcript: str, style: str) -> str:
    if style not in STYLE_CONTROLS:
        raise LMStudioResponseError("S1-mini does not support this writing style.")
    if len(transcript.encode("utf-8")) > MAX_CHUNK_BYTES:
        raise LMStudioResponseError("S1-mini input exceeds the per-request limit.")
    if any(token in transcript.lower() for token in ("<|", "|>", "<think", "</think")):
        raise LMStudioResponseError("S1-mini input contains model control tokens; using rules.")
    styling, structure, context = STYLE_CONTROLS[style]
    control = f"[Styling: {styling}] [Structure: {structure}] [Context: {context}]"
    return (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\n{control}\n{transcript}<|im_end|>\n"
        "<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )


def has_protected_speech(text: str, snippets: Mapping[str, str]) -> bool:
    """Keep commands and snippet triggers out of the specialized rewrite pass.

    S1-mini cannot follow JiSpr's usual preservation instructions. Use the
    existing parsers so source and generated text obey the same rules as
    downstream insertion, including rejecting newly invented key commands.
    """
    normalized = normalize_whitespace(text)
    without_commands, actions = apply_dictation_commands(text)
    _, code_count = apply_spoken_code_syntax(text)
    _, additions = extract_dictionary_additions(text)
    _, snippet_count = expand_snippets(text, snippets)
    return bool(
        actions or without_commands != normalized or code_count or additions or snippet_count
    )
