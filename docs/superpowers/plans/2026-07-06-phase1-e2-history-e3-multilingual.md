# Phase 1 Remainder: E2 Dictation History + E3 Multilingual ASR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Tasks 1–3 are E2 (sequential); Task 4 is E3 (independent of E2 but run after to avoid config.py collisions). Steps use checkbox syntax.

**Goal:** A local, append-only JSONL dictation history with retention control and a `local-flow history` CLI (E2), and auto/multilingual language support for the ASR stage with config validation (E3).

**Architecture:** `HistoryStore` is a new pure module (`local_flow/history/store.py`) wired into `DictationPipeline.process_transcript`; replacement counting extends the pure rules functions' return values; ASR language threads through the existing `Transcriber` constructors. Everything stays headless-testable with mocks.

**Tech Stack:** stdlib only for E2 (json, datetime, pathlib); faster-whisper (existing `asr` extra) for E3.

## Global Constraints

(Same as the roadmap's: local-only, mocks + headless tests for every path, lazy platform imports, Config-field + env + TOML pattern, `LocalFlowError` with hints, `uv run pytest` and `uv run ruff check .` green at every commit, line length 100.)

- History is an append-only JSONL file at `data_dir/history.jsonl`; corrupt lines are skipped tolerantly on read (same spirit as the JSON personalization files).
- All time handling is injectable for tests: `HistoryStore(..., now: Callable[[], datetime] | None = None)` — never call `datetime.now()` inline in logic under test.
- New config fields: `history_enabled: bool = True`, `history_max_entries: int = 5000`, `history_retention: str = "forever"  # forever | 24h | off`, `asr_language: str = "en"  # ISO code | auto`. Invalid `history_retention`/`asr_language`-model combos raise `ConfigError` with hints at load/build time as specified below.

---

### Task 1: `HistoryStore` + `HistoryRecord` + config fields (E2)

**Files:**
- Create: `local_flow/history/__init__.py` (re-export `HistoryStore`, `HistoryRecord`), `local_flow/history/store.py`
- Modify: `local_flow/config.py` (three new fields; `history_enabled` needs bool in `field_types`, `history_max_entries` int; validate `history_retention` value in `load_config` → `ConfigError` listing valid values)
- Test: `tests/test_history.py` (new), `tests/test_config.py` (retention validation)

**Interfaces (exact):**
```python
@dataclass
class HistoryRecord:
    timestamp: str  # ISO 8601, UTC
    rough: str
    final: str
    used_llm: bool = False
    app: str = ""          # filled by E4 later
    duration_s: float = 0.0
    replacements: int = 0

class HistoryStore:
    def __init__(self, data_dir: Path, max_entries: int = 5000,
                 retention: str = "forever",
                 now: Callable[[], datetime] | None = None) -> None: ...
    def append(self, record: HistoryRecord) -> None: ...   # no-op when retention == "off"
    def recent(self, limit: int = 20) -> list[HistoryRecord]: ...  # newest first
    def search(self, query: str, limit: int = 20) -> list[HistoryRecord]: ...  # case-insensitive substring on rough+final
    def all(self) -> Iterator[HistoryRecord]: ...
    def clear(self) -> None: ...  # delete the file
    @property
    def path(self) -> Path: ...   # data_dir / "history.jsonl"
```
- `append` creates `data_dir` if missing; rotation: when the file exceeds `max_entries` lines after append, rewrite keeping the newest `max_entries` (checking via a cheap line count is fine at this scale). `retention == "24h"`: on every append, prune records older than 24h relative to `now()`. `retention == "off"`: `append` writes nothing (file untouched).
- Reader skips unparseable lines and unknown fields tolerantly (forward compat).

**Behaviors to test (TDD; write each as a failing test first):** append→recent roundtrip (newest first); search case-insensitive across rough and final; rotation keeps newest N; 24h retention prunes (inject `now`); `off` writes nothing; corrupt line skipped; `clear` removes file and `recent` returns `[]`; config: default values, `LOCAL_FLOW_HISTORY_RETENTION=weekly` → `ConfigError` naming valid values.

- [ ] Tests → RED → implement → GREEN → `uv run pytest && uv run ruff check .` → commit `feat(history): JSONL history store with rotation and retention`

### Task 2: Replacement counting + pipeline wiring (E2)

**Files:**
- Modify: `local_flow/polish/rules.py` — `enforce_dictionary(text, terms) -> tuple[str, int]` and `expand_snippets(text, snippets) -> tuple[str, int]` (count = number of substitutions performed). Update ALL call sites: `local_flow/pipeline.py`, `local_flow/app.py` (`_cmd_polish`), `local_flow/commands/command_mode.py` if it calls them, and existing tests in `tests/test_polish_rules.py` / `tests/test_personalization.py`.
- Modify: `local_flow/pipeline.py` — `DictationPipeline(..., history: HistoryStore | None = None)`; `process_transcript(rough, duration_s: float = 0.0)`; `process_audio` computes `duration_s = len(pcm) / (2 * sample_rate)` summed over segments and passes it; after the insertion block (record regardless of `inserted` when there is any rough text), append `HistoryRecord(timestamp=now-iso, rough=..., final=..., used_llm=..., app="", duration_s=..., replacements=dict_count + snippet_count)`. Timestamp comes from the store's `now()` — give `HistoryStore` a `make_record(...)` helper or pass a `now` seam into the pipeline; choose ONE and keep it testable (recommended: `HistoryStore.append_new(**fields)` builds the timestamp internally from its injected `now`).
- Modify: `local_flow/app.py` — `_build_pipeline` constructs `HistoryStore` when `config.history_enabled` and passes it.
- Test: extend `tests/test_pipeline_integration.py` (a dictation through the mock pipeline lands in a tmp history store with duration + replacement counts; disabled store → file absent).

- [ ] Tests → RED → implement → GREEN → full suite → commit `feat(history): record every dictation with duration and replacement counts`

### Task 3: `local-flow history` subcommand + docs (E2)

**Files:**
- Modify: `local_flow/app.py` — subcommand `history` with `--search TEXT`, `--limit N` (default 20), `--clear`; output format one record per line: `2026-07-06T18:22:11Z  [llm]  "final text..."` (truncate final to ~80 chars, add `(raw: ...)` only with `--verbose`); `--clear` prints how the file was removed; respects `history_enabled=false` by printing a hint that history is disabled.
- Modify: `README.md` (Use section + a short "History & privacy" note: local file path, disable via env, clear command), `.env.example`, `local-flow.example.toml` (three new keys with comments).
- Test: `tests/test_demo_and_cli.py` style — invoke `main(["history", ...])` with a seeded store in a tmp data_dir (env override) and assert output.

- [ ] Tests → RED → implement → GREEN → full suite → commit `feat(history): local-flow history subcommand and docs`

### Task 4: E3 Multilingual ASR with auto detection

**Files:**
- Modify: `local_flow/config.py` — `asr_language: str = "en"`; no load-time validation of language codes (whisper accepts many), but the **combination** check lives in `_build_transcriber`.
- Modify: `local_flow/asr/base.py` (if it documents the interface, note language), `local_flow/asr/faster_whisper_asr.py` — constructor gains `language: str | None = "en"`; pass `language=self._language` into `model.transcribe(...)`; `"auto"` maps to `None` (whisper auto-detect).
- Modify: `local_flow/asr/mock.py` — accept and store an optional `language` kwarg (ignored) so builders can pass uniformly.
- Modify: `local_flow/app.py` — `_build_transcriber` passes language; raise `ConfigError` when `config.asr_language != "en"` (including `auto`) while `config.asr_model.endswith(".en")` — hint: "use a multilingual model such as `small`, not `small.en`"; `_cmd_check` prints the resolved model + language combo.
- Modify: `README.md` — model table gains multilingual rows (`small`, `medium`, `large-v3-turbo`) and a sentence on `LOCAL_FLOW_ASR_LANGUAGE=auto`; `.env.example` + `local-flow.example.toml` new key.
- Test: `tests/test_config.py` / a new `tests/test_asr_config.py` — builder-level validation (auto + small.en → ConfigError with hint; auto + small → OK constructing with mock backend; en + small.en → OK); mock transcriber accepts language kwarg.

- [ ] Tests → RED → implement → GREEN → full suite → commit `feat(asr): configurable language with auto-detection and model validation`

## Self-Review (done)

Interfaces are exact and mutually consistent (`HistoryRecord.duration_s`/`replacements` produced in Task 2, consumed later by E14; `app` field reserved for E4). Rules-function signature change is contained with all call sites listed. Time injection keeps everything deterministic. E3 validation sits in the builder (model+language combo is a runtime concern, not config parsing).
