"""Dictation pipeline: audio -> VAD -> ASR -> rules -> LLM polish -> insertion."""

from __future__ import annotations

from dataclasses import dataclass, field

from local_flow.asr.base import Transcriber
from local_flow.audio.vad import VoiceActivityDetector, split_segments
from local_flow.commands.command_mode import CommandMode
from local_flow.context.field_text import FieldTextProvider
from local_flow.context.router import ContextRouter, ResolvedContext
from local_flow.errors import LMStudioError
from local_flow.history.store import HistoryStore
from local_flow.insertion.base import TextSink
from local_flow.personalization.store import PersonalizationStore
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.polish.rules import (
    apply_dictation_commands,
    apply_spoken_code_syntax,
    enforce_dictionary,
    enforce_dictionary_detailed,
    expand_snippets,
    extract_dictionary_additions,
)


@dataclass
class DictationResult:
    rough: str
    cleaned: str
    polished: str
    final: str
    actions: list[str] = field(default_factory=list)
    used_llm: bool = False
    warnings: list[str] = field(default_factory=list)
    inserted: bool = False
    duration_s: float = 0.0


class DictationPipeline:
    def __init__(
        self,
        transcriber: Transcriber,
        polisher: TranscriptPolisher,
        store: PersonalizationStore,
        sink: TextSink,
        command_mode: CommandMode | None = None,
        history: HistoryStore | None = None,
        router: ContextRouter | None = None,
        auto_transform_prompt: str | None = None,
        field_text: FieldTextProvider | None = None,
    ) -> None:
        self.transcriber = transcriber
        self.polisher = polisher
        self.store = store
        self.sink = sink
        self.command_mode = command_mode
        self.history = history
        self.router = router
        # A resolved transform *prompt* (not a name -- the app layer resolves
        # `config.auto_transform` against `store.transforms()` once at build
        # time, see `local_flow.app._build_pipeline`), applied to the final
        # text right before insertion. `None` (the default) is a complete
        # no-op, byte-identical to before this feature existed.
        self.auto_transform_prompt = auto_transform_prompt
        # E10 field-text awareness (see `local_flow.context.field_text`):
        # best-effort reader of the focused field's existing text, consulted
        # once per utterance right alongside `router.resolve()` (see
        # `process_transcript`) and passed to `self.polisher.polish` as
        # `field_context`. `None` (the default, and what every non-desktop
        # build gets since `local_flow.app._build_pipeline` only constructs
        # one when `config.context_awareness` is on) is a complete no-op --
        # byte-identical to before this feature existed.
        self.field_text = field_text
        self.last_transcript: str = ""

    def process_audio(
        self,
        pcm: bytes,
        sample_rate: int,
        vad: VoiceActivityDetector | None = None,
        frame_ms: int = 30,
        silence_ms: int = 600,
        sink_override: TextSink | None = None,
    ) -> DictationResult:
        """Transcribe a PCM buffer (VAD-segmented when a VAD is given)."""
        if vad is not None:
            segments = split_segments(
                pcm, sample_rate, vad, frame_ms=frame_ms, silence_ms=silence_ms
            )
        else:
            segments = [pcm] if pcm else []
        texts = [self.transcriber.transcribe(seg, sample_rate) for seg in segments]
        rough = " ".join(t.strip() for t in texts if t.strip())
        duration_s = sum(len(seg) for seg in segments) / (2 * sample_rate) if sample_rate else 0.0
        return self.process_transcript(rough, duration_s=duration_s, sink_override=sink_override)

    def process_transcript(
        self,
        rough: str,
        duration_s: float = 0.0,
        sink_override: TextSink | None = None,
    ) -> DictationResult:
        """Run the text half of the pipeline and insert the result.

        ``sink_override``, when given, wins outright over BOTH the router's
        per-app sink override (``ctx.sink``, see ``ContextRouter``) and this
        pipeline's own configured ``self.sink`` -- this is what lets the
        scratchpad dictate-to-pad hotkey (see ``local_flow.app._run_loop``)
        force every insertion into the active note regardless of which app is
        frontmost. ``ctx`` (style, app_id for history) is still resolved and
        applied normally either way -- only the SINK portion of routing is
        overridden. ``None`` (the default) is byte-identical to before this
        parameter existed.
        """
        ctx = self.router.resolve() if self.router is not None else ResolvedContext()

        field_context = None
        if self.field_text is not None and self.polisher.level != "none":
            # Best-effort, consulted once per utterance right alongside the
            # router above (see `FieldTextProvider.current`'s never-raises
            # contract). Skipped entirely at cleanup_level="none": that
            # level never calls the LLM at all (see `TranscriptPolisher.
            # polish`), so there is nothing for a context block to feed and
            # reading the focused field would be pure overhead.
            field_context = self.field_text.current()

        polish = self.polisher.polish(rough, style=ctx.style, field_context=field_context)
        text, dict_counts = enforce_dictionary_detailed(
            polish.polished, self.store.dictionary_terms()
        )
        dict_count = sum(dict_counts.values())
        text, snippet_count = expand_snippets(text, self.store.snippets())
        text, actions = apply_dictation_commands(text)
        code_count = 0
        if self.polisher.level != "none":
            # Skipped at cleanup_level="none": that level is a full verbatim
            # bypass (see TranscriptPolisher.polish), so spoken code-syntax
            # phrases are left as literally spoken rather than converted.
            text, code_count = apply_spoken_code_syntax(text)
        text, dictionary_additions = extract_dictionary_additions(text)

        result = DictationResult(
            rough=rough,
            cleaned=polish.cleaned,
            polished=polish.polished,
            final=text,
            actions=actions,
            used_llm=polish.used_llm,
            warnings=list(polish.warnings),
            duration_s=duration_s,
        )
        for term in dictionary_additions:
            if self.store.add_dictionary_term(term):
                result.warnings.append(f"added '{term}' to dictionary")
            else:
                result.warnings.append(f"'{term}' already in dictionary")

        if (
            self.auto_transform_prompt
            and text
            and self.polisher.chat_client is not None
            and self.polisher.level != "none"
        ):
            # Runs after every personalization step (dictionary/snippets/
            # dictation commands/spoken code syntax) and right before
            # insertion -- so it sees exactly what would otherwise have been
            # typed/pasted. `cleanup_level="none"` is a full verbatim bypass
            # (see TranscriptPolisher.polish) and is honored here too: no
            # chat client means there's nothing to run it through, and
            # "none" means the user asked for hands-off output.
            try:
                from local_flow.transforms.registry import apply_transform

                text = apply_transform(
                    self.polisher.chat_client, self.auto_transform_prompt, text
                )
                result.final = text
            except LMStudioError as exc:
                # Degrade, don't block: the original (pre-transform) text
                # still gets inserted, with a warning explaining why.
                result.warnings.append(f"auto-transform skipped: {exc.message}")

        sink = sink_override if sink_override is not None else (ctx.sink or self.sink)
        if text or actions:
            if text:
                sink.insert(text)
            for action in actions:
                sink.press_key(action)
            result.inserted = True
            if text:
                self.last_transcript = text

        if dict_counts and self.store is not None:
            self.store.record_term_uses(dict_counts)

        if rough and self.history is not None:
            # A chat client was configured (so the user expects LLM polish)
            # but it was never actually used -- either it raised/was
            # unreachable, or something else skipped it. At
            # cleanup_level="none" the client is never invoked by design
            # (see TranscriptPolisher.polish), so that case is excluded:
            # skipping the LLM there isn't a failure, it's the whole point.
            failed = (
                self.polisher.chat_client is not None
                and not result.used_llm
                and self.polisher.level != "none"
            )
            self.history.append_new(
                rough=rough,
                final=result.final,
                used_llm=result.used_llm,
                app=ctx.app_id,
                duration_s=duration_s,
                replacements=dict_count + snippet_count + code_count,
                failed=failed,
            )

        return result

    def run_command(self, instruction: str, target_text: str | None = None) -> str:
        """Command mode: transform target (or last transcript) and insert it."""
        if self.command_mode is None:
            raise ValueError("Command mode is not configured for this pipeline.")
        transformed = self.command_mode.run(
            instruction,
            target_text=target_text,
            last_transcript=self.last_transcript,
        )
        transformed, _dict_count = enforce_dictionary(transformed, self.store.dictionary_terms())
        if transformed:
            self.sink.insert(transformed)
        return transformed
