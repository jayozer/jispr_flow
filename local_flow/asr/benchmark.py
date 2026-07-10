"""Repeatable, local ASR benchmark harness with no model dependency of its own."""

from __future__ import annotations

import json
import math
import platform
import re
import statistics
import sys
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from local_flow.asr.base import Transcriber
from local_flow.errors import LocalFlowError

_WORD = re.compile(r"\w+", re.UNICODE)


def normalized_words(text: str) -> list[str]:
    """Case-fold text into Unicode word tokens for deterministic WER scoring."""
    return _WORD.findall(text.casefold())


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Compute Levenshtein word-error rate without an optional metrics package."""
    errors, expected_count = _word_error_counts(reference, hypothesis)
    if not expected_count:
        return 0.0 if errors == 0 else 1.0
    return errors / expected_count


def _word_error_counts(reference: str, hypothesis: str) -> tuple[int, int]:
    expected = normalized_words(reference)
    actual = normalized_words(hypothesis)
    if not expected:
        return (0 if not actual else 1), 0

    previous = list(range(len(actual) + 1))
    for row, expected_word in enumerate(expected, start=1):
        current = [row]
        for column, actual_word in enumerate(actual, start=1):
            substitution = previous[column - 1] + (expected_word != actual_word)
            insertion = current[column - 1] + 1
            deletion = previous[column] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return previous[-1], len(expected)


def audio_duration_s(path: Path) -> float:
    """Read audio duration from WAV directly or PyAV for other containers."""
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as wav_file:
            rate = wav_file.getframerate()
            return wav_file.getnframes() / rate if rate else 0.0
    except (EOFError, OSError, wave.Error):
        pass

    try:
        import av
    except ImportError as exc:
        raise LocalFlowError(
            f"Could not determine audio duration for {path.name!r}.",
            hint="Use a WAV file, or install the ASR extra so PyAV can inspect "
            "MP3/M4A/FLAC containers: `uv sync --extra asr`.",
        ) from exc

    try:
        with av.open(str(path)) as container:
            if container.duration is not None:
                return float(container.duration * av.time_base)
            for stream in container.streams.audio:
                if stream.duration is not None and stream.time_base is not None:
                    return float(stream.duration * stream.time_base)
    except Exception as exc:
        raise LocalFlowError(
            f"Could not determine audio duration for {path.name!r}: {exc}",
            hint="Check that the file is a supported, readable audio container.",
        ) from exc
    raise LocalFlowError(
        f"Audio duration is unavailable for {path.name!r}.",
        hint="Use a file whose container includes duration metadata.",
    )


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


@dataclass(frozen=True)
class BenchmarkRun:
    index: int
    latency_s: float
    real_time_factor: float
    transcript: str
    wer: float | None = None


@dataclass(frozen=True)
class BenchmarkFile:
    path: str
    duration_s: float
    median_latency_s: float
    p95_latency_s: float
    median_real_time_factor: float
    transcript: str
    wer: float | None
    runs: list[BenchmarkRun]


@dataclass(frozen=True)
class BenchmarkReport:
    schema_version: int
    backend: str
    model: str
    device: str
    compute_type: str
    model_load_time_s: float
    warmup_runs: int
    measured_runs: int
    aggregate_median_latency_s: float
    aggregate_wer: float | None
    environment: dict[str, str]
    files: list[BenchmarkFile]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n"


def benchmark_files(
    paths: Sequence[Path],
    build_transcriber: Callable[[], Transcriber],
    *,
    backend: str,
    model: str,
    device: str,
    compute_type: str,
    references: Sequence[str | None] | None = None,
    runs: int = 3,
    warmup_runs: int = 1,
    clock: Callable[[], float] = time.perf_counter,
) -> BenchmarkReport:
    """Load one model, warm it, and benchmark every file repeatedly."""
    if not paths:
        raise LocalFlowError("ASR benchmark needs at least one audio file.")
    if runs < 1:
        raise LocalFlowError("ASR benchmark --runs must be at least 1.")
    if warmup_runs < 0:
        raise LocalFlowError("ASR benchmark --warmup must be 0 or greater.")
    references = list(references) if references is not None else [None] * len(paths)
    if len(references) != len(paths):
        raise LocalFlowError(
            "ASR benchmark references must match the number of audio files.",
            hint="Repeat `--reference TEXT` once per FILE, in the same order.",
        )

    load_started = clock()
    transcriber = build_transcriber()
    transcriber.prepare()
    model_load_time_s = clock() - load_started
    transcribe_path = getattr(transcriber, "transcribe_path", None)
    if not callable(transcribe_path):
        raise LocalFlowError(
            f"ASR backend {backend!r} does not support file transcription.",
            hint="Choose a backend with a `transcribe_path` implementation.",
        )

    for index in range(warmup_runs):
        transcribe_path(Path(paths[index % len(paths)]))

    file_results: list[BenchmarkFile] = []
    for path, reference in zip(paths, references, strict=True):
        duration = audio_duration_s(path)
        measured: list[BenchmarkRun] = []
        for index in range(1, runs + 1):
            started = clock()
            transcript = str(transcribe_path(Path(path))).strip()
            latency = clock() - started
            measured.append(
                BenchmarkRun(
                    index=index,
                    latency_s=latency,
                    real_time_factor=latency / duration if duration else 0.0,
                    transcript=transcript,
                    wer=word_error_rate(reference, transcript)
                    if reference is not None
                    else None,
                )
            )
        latencies = [run.latency_s for run in measured]
        rtfs = [run.real_time_factor for run in measured]
        file_results.append(
            BenchmarkFile(
                path=str(path),
                duration_s=duration,
                median_latency_s=statistics.median(latencies),
                p95_latency_s=_percentile(latencies, 0.95),
                median_real_time_factor=statistics.median(rtfs),
                transcript=measured[-1].transcript,
                wer=measured[-1].wer,
                runs=measured,
            )
        )

    total_errors = 0
    total_reference_words = 0
    for reference, result in zip(references, file_results, strict=True):
        if reference is None:
            continue
        errors, word_count = _word_error_counts(reference, result.transcript)
        total_errors += errors
        total_reference_words += word_count

    return BenchmarkReport(
        schema_version=1,
        backend=backend,
        model=model,
        device=device,
        compute_type=compute_type,
        model_load_time_s=model_load_time_s,
        warmup_runs=warmup_runs,
        measured_runs=runs,
        aggregate_median_latency_s=statistics.median(
            result.median_latency_s for result in file_results
        ),
        aggregate_wer=(
            total_errors / total_reference_words if total_reference_words else None
        ),
        environment={
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
        },
        files=file_results,
    )


def render_report(report: BenchmarkReport) -> str:
    """Render a compact report while JSON retains full per-run precision."""
    lines = [
        "ASR benchmark",
        f"  backend/model : {report.backend} / {report.model}",
        f"  device/compute: {report.device} / {report.compute_type}",
        f"  model load    : {report.model_load_time_s:.3f}s",
        f"  runs          : {report.measured_runs} measured, {report.warmup_runs} warmup",
        f"  aggregate     : median latency {report.aggregate_median_latency_s:.3f}s, "
        + (
            f"WER {report.aggregate_wer:.3f}"
            if report.aggregate_wer is not None
            else "WER n/a"
        ),
    ]
    for result in report.files:
        lines.extend(
            [
                "",
                f"  {result.path} ({result.duration_s:.3f}s audio)",
                f"    latency     : median {result.median_latency_s:.3f}s, "
                f"p95 {result.p95_latency_s:.3f}s",
                f"    median RTF  : {result.median_real_time_factor:.3f}",
                f"    WER         : {result.wer:.3f}"
                if result.wer is not None
                else "    WER         : n/a (no reference)",
                f"    transcript  : {result.transcript}",
            ]
        )
        for run in result.runs:
            lines.append(
                f"    run {run.index:<3}   : {run.latency_s:.3f}s, "
                f"RTF {run.real_time_factor:.3f}"
            )
    return "\n".join(lines)
