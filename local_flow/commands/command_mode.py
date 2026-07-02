"""Command mode: apply a spoken/typed instruction to some target text.

The target is either explicitly provided (e.g. the current selection, read
via the clipboard in real desktop use) or falls back to the most recent
dictation transcript.
"""

from __future__ import annotations

from collections.abc import Iterable

from local_flow.llm.base import ChatClient, Message

COMMAND_SYSTEM_PROMPT = (
    "You are a local text-transformation assistant. Apply the user's "
    "instruction to the target text. Return ONLY the transformed text - no "
    "preamble, no quotes, no markdown fences, no explanations. If the "
    "instruction cannot be applied, return the target text unchanged."
)


def build_command_messages(
    instruction: str,
    target_text: str,
    dictionary_terms: Iterable[str] = (),
    style_rules: str = "",
) -> list[Message]:
    system = COMMAND_SYSTEM_PROMPT
    terms = [t for t in dictionary_terms if t.strip()]
    if terms:
        system += "\nSpell these terms exactly as given: " + ", ".join(terms) + "."
    if style_rules:
        system += f"\nWriting style: {style_rules}"
    user = f"Instruction: {instruction}\n\nTarget text:\n{target_text}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


class CommandMode:
    def __init__(
        self,
        chat_client: ChatClient,
        dictionary_terms: Iterable[str] = (),
        style_rules: str = "",
    ) -> None:
        self.chat_client = chat_client
        self.dictionary_terms = list(dictionary_terms)
        self.style_rules = style_rules

    def run(
        self,
        instruction: str,
        target_text: str | None = None,
        last_transcript: str | None = None,
    ) -> str:
        """Transform ``target_text`` (or the last transcript) per ``instruction``."""
        instruction = instruction.strip()
        if not instruction:
            raise ValueError("Command mode needs a non-empty instruction.")
        target = (target_text or "").strip() or (last_transcript or "").strip()
        if not target:
            raise ValueError(
                "Command mode has no target text: select/provide text or "
                "dictate something first."
            )
        messages = build_command_messages(
            instruction,
            target,
            dictionary_terms=self.dictionary_terms,
            style_rules=self.style_rules,
        )
        return self.chat_client.chat(messages)
