"""Headless demo: proves the full pipeline with mocks, no permissions needed.

Runs synthetic PCM through the EnergyVAD, a scripted mock transcriber, the
rule cleanup, a mock LM Studio client, personalization (dictionary, snippets,
style), dictation commands, and a fake text sink - then does the same for
command mode. Prints before/after at each stage.

Run with: ``uv run local-flow demo``
"""

from __future__ import annotations

import math
import struct
import tempfile
from pathlib import Path

from local_flow.asr.mock import MockTranscriber
from local_flow.audio.vad import EnergyVAD
from local_flow.commands.command_mode import CommandMode
from local_flow.insertion.base import FakeTextSink
from local_flow.llm.mock import MockChatClient
from local_flow.personalization.store import PersonalizationStore
from local_flow.pipeline import DictationPipeline
from local_flow.polish.polisher import TranscriptPolisher

SAMPLE_RATE = 16000

# Fixture: what the mock ASR "hears" in each detected speech segment.
FIXTURE_SEGMENT_TEXTS = [
    "um so the jispr flow launch notes uh go to john, scratch that, go to sarah.",
    "uh also please double check the postgresql migration. add sig block press enter",
]

# Fixture: canned LM Studio replies (1st: polish pass, 2nd: command mode).
FIXTURE_LLM_RESPONSES = [
    "So the JiSpr Flow launch notes go to Sarah. Also, please double-check the "
    "postgresql migration. Add sig block press enter",
    "- Send the JiSpr Flow launch notes to Sarah\n"
    "- Double-check the PostgreSQL migration",
]

FIXTURE_COMMAND_INSTRUCTION = "Rewrite this as a short bullet list for the team channel."

DEMO_DICTIONARY = ["JiSpr Flow", "PostgreSQL"]
DEMO_SNIPPETS = {"sig block": "Best regards,\nJay"}


def synth_pcm(spec: list[tuple[int, int]], sample_rate: int = SAMPLE_RATE) -> bytes:
    """Build 16-bit PCM from (duration_ms, amplitude) pairs (sine or silence)."""
    chunks: list[bytes] = []
    for duration_ms, amplitude in spec:
        n = int(sample_rate * duration_ms / 1000)
        if amplitude == 0:
            chunks.append(b"\x00\x00" * n)
        else:
            samples = (
                int(amplitude * math.sin(2 * math.pi * 220 * i / sample_rate))
                for i in range(n)
            )
            chunks.append(struct.pack(f"<{n}h", *samples))
    return b"".join(chunks)


def run_demo() -> int:
    print("=== local-flow headless demo (mocked ASR/VAD/LM Studio, fake sink) ===\n")

    with tempfile.TemporaryDirectory(prefix="local-flow-demo-") as tmp:
        store = PersonalizationStore(Path(tmp))
        for term in DEMO_DICTIONARY:
            store.add_dictionary_term(term)
        for trigger, expansion in DEMO_SNIPPETS.items():
            store.set_snippet(trigger, expansion)

        llm = MockChatClient(list(FIXTURE_LLM_RESPONSES))
        sink = FakeTextSink()
        polisher = TranscriptPolisher(llm, store)
        command_mode = CommandMode(llm, dictionary_terms=store.dictionary_terms())
        pipeline = DictationPipeline(
            transcriber=MockTranscriber(FIXTURE_SEGMENT_TEXTS),
            polisher=polisher,
            store=store,
            sink=sink,
            command_mode=command_mode,
        )

        # --- [1] Dictation ------------------------------------------------
        # Two speech bursts separated by silence; EnergyVAD finds both.
        pcm = synth_pcm(
            [(200, 0), (900, 12000), (800, 0), (900, 12000), (300, 0)]
        )
        vad = EnergyVAD(threshold=500.0)
        result = pipeline.process_audio(pcm, SAMPLE_RATE, vad=vad, silence_ms=400)

        print("[1] Dictation: rough transcript -> polished, inserted text")
        print(f"  synthetic audio      : {len(pcm)} bytes, "
              f"{len(pipeline.transcriber.calls)} VAD speech segments transcribed")
        print(f"  rough (mock ASR)     : {result.rough!r}")
        print(f"  after rule cleanup   : {result.cleaned!r}")
        print(f"  after LM Studio      : {result.polished!r}  (mock client)")
        print(f"  final inserted text  : {result.final!r}")
        print(f"  key actions          : {result.actions}")
        print(f"  fake sink received   : {sink.events}")
        print()

        # --- [2] Command mode ----------------------------------------------
        print("[2] Command mode: transform last transcript -> inserted text")
        print(f"  instruction          : {FIXTURE_COMMAND_INSTRUCTION!r}")
        print(f"  target (last dictation): {pipeline.last_transcript!r}")
        transformed = pipeline.run_command(FIXTURE_COMMAND_INSTRUCTION)
        print(f"  transformed (mock LM Studio): {transformed!r}")
        print(f"  fake sink document now:\n---\n{sink.text}\n---")
        print()

        # --- sanity checks so 'exit 0' actually proves something -----------
        checks = {
            "fillers removed": "um" not in result.cleaned.split(),
            "backtracking applied": "john" not in result.final.lower(),
            "dictionary enforced": "PostgreSQL" in result.final
            and "JiSpr Flow" in result.final,
            "snippet expanded": "Best regards,\nJay" in result.final,
            "press enter became a key action": result.actions == ["enter"],
            "dictation text inserted": any(k == "insert" for k, _ in sink.events),
            "command output inserted": sink.events[-1] == ("insert", transformed),
        }
        failed = [name for name, ok in checks.items() if not ok]
        for name, ok in checks.items():
            print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if failed:
            print(f"\ndemo FAILED: {', '.join(failed)}")
            return 1

    print("\ndemo completed successfully (no mic, hotkey, model, or server needed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_demo())
