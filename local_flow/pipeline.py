"""Dictation pipeline: audio -> VAD -> ASR -> rules -> LLM polish -> insertion."""

from __future__ import annotations

from dataclasses import dataclass, field

from local_flow.asr.base import Transcriber
from local_flow.audio.vad import VoiceActivityDetector, split_segments
from local_flow.commands.command_mode import CommandMode
from local_flow.context.router import ContextRouter, ResolvedContext
from local_flow.history.store import HistoryStore
from local_flow.insertion.base import TextSink
from local_flow.personalization.store import PersonalizationStore
from local_flow.polish.polisher import TranscriptPolisher
from local_flow.polish.rules import (
    apply_dictation_commands,
    enforce_dictionary,
    expand_snippets,
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
    ) -> None:
        self.transcriber = transcriber
        self.polisher = polisher
        self.store = store
        self.sink = sink
        self.command_mode = command_mode
        self.history = history
        self.router = router
        self.last_transcript: str = ""

    def process_audio(
        self,
        pcm: bytes,
        sample_rate: int,
        vad: VoiceActivityDetector | None = None,
        frame_ms: int = 30,
        silence_ms: int = 600,
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
        return self.process_transcript(rough, duration_s=duration_s)

    def process_transcript(self, rough: str, duration_s: float = 0.0) -> DictationResult:
        """Run the text half of the pipeline and insert the result."""
        ctx = self.router.resolve() if self.router is not None else ResolvedContext()

        polish = self.polisher.polish(rough, style=ctx.style)
        text, dict_count = enforce_dictionary(polish.polished, self.store.dictionary_terms())
        text, snippet_count = expand_snippets(text, self.store.snippets())
        text, actions = apply_dictation_commands(text)

        result = DictationResult(
            rough=rough,
            cleaned=polish.cleaned,
            polished=polish.polished,
            final=text,
            actions=actions,
            used_llm=polish.used_llm,
            warnings=list(polish.warnings),
        )
        sink = ctx.sink or self.sink
        if text or actions:
            if text:
                sink.insert(text)
            for action in actions:
                sink.press_key(action)
            result.inserted = True
            if text:
                self.last_transcript = text

        if rough and self.history is not None:
            self.history.append_new(
                rough=rough,
                final=result.final,
                used_llm=result.used_llm,
                app=ctx.app_id,
                duration_s=duration_s,
                replacements=dict_count + snippet_count,
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
