"""Named text transforms: apply a stored prompt to arbitrary text via an LLM.

A transform is just a system-prompt instruction (e.g. "Rewrite for clarity
and concision") applied to whatever text it's given -- the same LM Studio
chat-completion convention :class:`~local_flow.polish.polisher.TranscriptPolisher`
and :class:`~local_flow.commands.command_mode.CommandMode` already use, reused
here instead of inventing a fourth prompt-building convention. Transform
*names* and prompts live in ``transforms.json``
(:meth:`~local_flow.personalization.store.PersonalizationStore.transforms`);
this module only knows how to run one given its prompt text.
"""

from __future__ import annotations

from local_flow.llm.base import ChatClient, Message

_RETURN_ONLY_SUFFIX = "Return ONLY the transformed text."


def build_transform_messages(prompt: str, text: str) -> list[Message]:
    """Build the ``[system, user]`` messages for one transform application.

    ``prompt`` becomes the system message with an explicit "return only the
    text" instruction appended (transforms are otherwise free to end their
    own prompt however they like); ``text`` is passed through untouched as
    the user message.
    """
    system = f"{prompt.strip()} {_RETURN_ONLY_SUFFIX}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]


def apply_transform(chat_client: ChatClient, prompt: str, text: str) -> str:
    """Apply ``prompt`` (a transform's stored instruction) to ``text``.

    Unlike :class:`~local_flow.polish.polisher.TranscriptPolisher`, there is
    no rule-based fallback here -- an LLM failure (any
    :class:`~local_flow.errors.LMStudioError` subclass) propagates straight
    to the caller, which decides whether to fail, warn, or skip.
    """
    messages = build_transform_messages(prompt, text)
    return chat_client.chat(messages)
