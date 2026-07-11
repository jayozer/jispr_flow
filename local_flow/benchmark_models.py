"""Two-stage local model benchmark: freeze ASR, then compare polishers fairly."""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from local_flow.asr.base import Transcriber
from local_flow.asr.benchmark import audio_duration_s, word_error_rate
from local_flow.errors import LocalFlowError
from local_flow.llm.lmstudio import LMStudioClient, StreamResult
from local_flow.personalization.store import PersonalizationStore
from local_flow.polish.prompting import build_polish_messages
from local_flow.polish.rules import (
    apply_dictation_commands,
    apply_spoken_code_syntax,
    clean_transcript,
    enforce_dictionary,
    expand_snippets,
)

_PUNCTUATION = re.compile(r"[.,!?;:]")


@dataclass(frozen=True)
class CorpusCase:
    id: str
    audio: str
    language: str
    verbatim: str
    intended: str
    category: str
    proper_names: list[str]
    protected_tokens: list[str]
    fillers: list[str]


@dataclass(frozen=True)
class FrozenTranscript:
    case_id: str
    audio: str
    language: str
    raw: str
    asr_latency_s: float
    audio_duration_s: float
    wer: float


def load_corpus(path: Path) -> list[CorpusCase]:
    """Load the private JSONL manifest, resolving audio paths beside it."""
    cases: list[CorpusCase] = []
    seen: set[str] = set()
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LocalFlowError(f"Could not read benchmark manifest {path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except ValueError as exc:
            raise LocalFlowError(
                f"Invalid JSON on benchmark manifest line {line_number}: {exc}"
            ) from exc
        required = {"id", "audio", "language", "verbatim", "intended", "category"}
        missing = sorted(required - set(raw)) if isinstance(raw, dict) else sorted(required)
        if missing:
            raise LocalFlowError(
                f"Benchmark manifest line {line_number} is missing: {', '.join(missing)}"
            )
        case_id = str(raw["id"]).strip()
        if not case_id or case_id in seen:
            raise LocalFlowError(f"Benchmark case id must be unique and non-empty: {case_id!r}")
        seen.add(case_id)
        audio = Path(str(raw["audio"])).expanduser()
        if not audio.is_absolute():
            audio = Path(path).parent / audio
        if not audio.is_file():
            raise LocalFlowError(f"Benchmark audio does not exist: {audio}")
        cases.append(
            CorpusCase(
                id=case_id,
                audio=str(audio),
                language=str(raw["language"]),
                verbatim=str(raw["verbatim"]),
                intended=str(raw["intended"]),
                category=str(raw["category"]),
                proper_names=[str(item) for item in raw.get("proper_names", [])],
                protected_tokens=[str(item) for item in raw.get("protected_tokens", [])],
                fillers=[str(item) for item in raw.get("fillers", [])],
            )
        )
    if not cases:
        raise LocalFlowError("Benchmark manifest contains no cases.")
    return cases


def freeze_asr(
    cases: Sequence[CorpusCase],
    build_transcriber: Callable[[], Transcriber],
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[float, list[FrozenTranscript]]:
    """Transcribe each case exactly once; these rows feed every polisher."""
    started = clock()
    transcriber = build_transcriber()
    transcriber.prepare()
    load_s = clock() - started
    transcribe_path = getattr(transcriber, "transcribe_path", None)
    if not callable(transcribe_path):
        raise LocalFlowError("Selected ASR backend does not support file transcription.")
    rows: list[FrozenTranscript] = []
    for case in cases:
        started = clock()
        raw = str(transcribe_path(Path(case.audio))).strip()
        latency = clock() - started
        rows.append(
            FrozenTranscript(
                case_id=case.id,
                audio=case.audio,
                language=case.language,
                raw=raw,
                asr_latency_s=latency,
                audio_duration_s=audio_duration_s(Path(case.audio)),
                wer=word_error_rate(case.verbatim, raw),
            )
        )
    return load_s, rows


def write_jsonl(path: Path, rows: Sequence[object]) -> None:
    from local_flow.atomicio import atomic_write_text

    text = "".join(
        json.dumps(row if isinstance(row, dict) else asdict(row), ensure_ascii=False) + "\n"
        for row in rows
    )
    atomic_write_text(path, text)


def load_frozen(path: Path) -> list[FrozenTranscript]:
    try:
        raw_rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
        return [FrozenTranscript(**row) for row in raw_rows]
    except (OSError, TypeError, ValueError) as exc:
        raise LocalFlowError(f"Could not read frozen ASR results {path}: {exc}") from exc


def _ratio(items: Sequence[str], text: str, *, case_sensitive: bool = True) -> float:
    if not items:
        return 1.0
    haystack = text if case_sensitive else text.casefold()
    hits = sum(
        1 for item in items if (item if case_sensitive else item.casefold()) in haystack
    )
    return hits / len(items)


def punctuation_f1(expected: str, actual: str) -> float:
    expected_counts = Counter(_PUNCTUATION.findall(expected))
    actual_counts = Counter(_PUNCTUATION.findall(actual))
    if not expected_counts and not actual_counts:
        return 1.0
    true_positive = sum((expected_counts & actual_counts).values())
    precision = true_positive / sum(actual_counts.values()) if actual_counts else 0.0
    recall = true_positive / sum(expected_counts.values()) if expected_counts else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def filler_removal_rate(fillers: Sequence[str], final: str) -> float:
    if not fillers:
        return 1.0
    remaining = 0
    for filler in fillers:
        if re.search(rf"(?i)(?<!\w){re.escape(filler)}(?!\w)", final):
            remaining += 1
    return 1.0 - remaining / len(fillers)


def _finalize(text: str, store: PersonalizationStore, *, cleanup_level: str) -> str:
    text, _ = enforce_dictionary(text, store.dictionary_terms())
    text, _ = expand_snippets(text, store.snippets())
    text, _actions = apply_dictation_commands(text)
    if cleanup_level != "none":
        text, _ = apply_spoken_code_syntax(text)
    return text


def _blind_id(model: str, case_id: str, raw: str, final: str) -> str:
    """Bind a blind review id to the exact input and output being reviewed."""
    payload = f"{model}\0{case_id}\0{raw}\0{final}"
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def benchmark_polishers(
    cases: Sequence[CorpusCase],
    frozen: Sequence[FrozenTranscript],
    models: Sequence[str],
    client_factory: Callable[[str], LMStudioClient],
    store: PersonalizationStore,
    *,
    cleanup_level: str,
    style: str,
    system_prompt: str,
    runs: int = 1,
) -> dict:
    """Polish byte-identical frozen transcripts through each local model."""
    if runs < 1:
        raise LocalFlowError("Model benchmark runs must be at least 1.")
    if not models:
        raise LocalFlowError("Model benchmark needs at least one --polisher model id.")
    case_by_id = {case.id: case for case in cases}
    frozen_by_id = {row.case_id: row for row in frozen}
    if set(case_by_id) != set(frozen_by_id):
        raise LocalFlowError("Frozen ASR case ids must exactly match the benchmark manifest.")
    style_name, style_rules = store.style_rules(style)
    results: list[dict] = []
    reviews: list[dict] = []

    for model in models:
        client = client_factory(model)
        try:
            for case in cases:
                row = frozen_by_id[case.id]
                cleaned = clean_transcript(row.raw)
                messages = build_polish_messages(
                    cleaned,
                    dictionary_terms=store.dictionary_terms(),
                    style_name=style_name,
                    style_rules=style_rules,
                    level=cleanup_level,
                    additional_system_prompt=system_prompt,
                )
                streamed: list[StreamResult] = [
                    client.chat_stream(messages) for _index in range(runs)
                ]
                polished = streamed[0].text
                final = _finalize(polished, store, cleanup_level=cleanup_level)
                protected_corruption = any(
                    token in row.raw and token not in final for token in case.protected_tokens
                )
                blind_id = _blind_id(model, case.id, row.raw, final)
                results.append(
                    {
                        "model": model,
                        "case_id": case.id,
                        "blind_id": blind_id,
                        "language": case.language,
                        "category": case.category,
                        "raw": row.raw,
                        "cleaned": cleaned,
                        "polished": polished,
                        "final": final,
                        "wer": row.wer,
                        "proper_name_accuracy": _ratio(case.proper_names, final),
                        "punctuation_f1": punctuation_f1(case.intended, final),
                        "filler_removal": filler_removal_rate(case.fillers, final),
                        "protected_token_accuracy": _ratio(case.protected_tokens, final),
                        "protected_corruption": protected_corruption,
                        "first_token_s": [item.first_token_s for item in streamed],
                        "polish_total_s": [item.total_s for item in streamed],
                        "post_capture_s": row.asr_latency_s + streamed[0].total_s,
                        "capture_to_insertion_s": (
                            row.audio_duration_s + row.asr_latency_s + streamed[0].total_s
                        ),
                    }
                )
                reviews.append(
                    {
                        "blind_id": blind_id,
                        "input": row.raw,
                        "output": final,
                        "material_meaning_change": None,
                        "hallucination": None,
                        "notes": "",
                    }
                )
        finally:
            client.close()

    aggregates = []
    for model in models:
        model_rows = [row for row in results if row["model"] == model]
        means = {
            key: statistics.fmean(row[key] for row in model_rows)
            for key in (
                "wer",
                "proper_name_accuracy",
                "punctuation_f1",
                "filler_removal",
                "protected_token_accuracy",
            )
        }
        quality = (
            0.25 * (1.0 - min(means["wer"], 1.0))
            + 0.20 * means["proper_name_accuracy"]
            + 0.15 * means["punctuation_f1"]
            + 0.10 * means["filler_removal"]
            + 0.30 * means["protected_token_accuracy"]
        )
        aggregates.append(
            {
                "model": model,
                **means,
                "quality_score": quality,
                "median_first_token_s": statistics.median(
                    timing for row in model_rows for timing in row["first_token_s"]
                ),
                "median_polish_total_s": statistics.median(
                    timing for row in model_rows for timing in row["polish_total_s"]
                ),
                "protected_corruptions": sum(
                    bool(row["protected_corruption"]) for row in model_rows
                ),
            }
        )
    return {
        "schema_version": 2,
        "models": list(models),
        "runs": runs,
        "results": results,
        "aggregates": aggregates,
        "blind_reviews": reviews,
        "recommendation": None,
        "review_status": "pending",
    }


def load_benchmark_report(path: Path) -> dict:
    """Load the saved outputs that a completed blind-review sheet describes."""
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LocalFlowError(f"Could not read benchmark report {path}: {exc}") from exc
    required = {"models", "results", "aggregates", "blind_reviews"}
    if not isinstance(report, dict) or not required.issubset(report):
        raise LocalFlowError(f"Invalid benchmark report: {path}")
    if not all(isinstance(report[field], list) for field in required):
        raise LocalFlowError(f"Invalid benchmark report collections: {path}")
    if report.get("schema_version") != 2:
        raise LocalFlowError(
            "Benchmark report does not bind reviews to exact outputs; rerun the benchmark."
        )
    if not all(isinstance(row, dict) for row in report["results"]):
        raise LocalFlowError(f"Invalid benchmark result rows: {path}")
    return report


def apply_reviews(report: dict, reviews: Sequence[dict]) -> dict:
    """Apply completed blind reviews and choose the safety-gated winner."""
    by_id = {str(item.get("blind_id")): item for item in reviews}
    expected = {row["blind_id"] for row in report["results"]}
    if set(by_id) != expected:
        raise LocalFlowError("Completed review ids must exactly match benchmark blind ids.")
    unsafe_models: set[str] = set()
    for row in report["results"]:
        review = by_id[row["blind_id"]]
        if review.get("material_meaning_change") is not False:
            unsafe_models.add(row["model"])
        if review.get("hallucination") is not False:
            unsafe_models.add(row["model"])
        if row["protected_corruption"]:
            unsafe_models.add(row["model"])
    eligible = [row for row in report["aggregates"] if row["model"] not in unsafe_models]
    winner = None
    if eligible:
        best_quality = max(row["quality_score"] for row in eligible)
        shortlist = [row for row in eligible if best_quality - row["quality_score"] <= 0.02]
        winner = min(shortlist, key=lambda row: row["median_polish_total_s"])["model"]
    updated = dict(report)
    updated["recommendation"] = winner
    updated["review_status"] = "complete"
    updated["unsafe_models"] = sorted(unsafe_models)
    return updated
