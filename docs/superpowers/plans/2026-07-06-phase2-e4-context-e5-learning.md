# Phase 2: E4 Per-App Awareness + E5 Auto-Learning Dictionary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Tasks run in order 1→5 (T1–T3 are E4, T4–T5 are E5; T3 depends on T1+T2; T5 depends on T4 and on E2's history store).

**Goal:** Polish style and insertion method follow the frontmost app (E4), and the dictionary learns from usage: starred terms, usage ranking, history mining via `local-flow learn`, and a spoken "add … to dictionary" command (E5).

**Architecture:** A new `local_flow/context/` package holds the frontmost-app adapters and a small `ContextRouter` that resolves (style, sink, app_id) per utterance; the pipeline consults it at `process_transcript` time. The dictionary file gains a richer (backward-compatible) entry format handled entirely inside `PersonalizationStore`.

## Global Constraints

Same as always: local-only; every adapter lazily imported with a mock for CI; Config+env+TOML pattern; `LocalFlowError` message+hint; `uv run pytest` + `uv run ruff check .` green at each commit; line length 100. Platform deps: `pyobjc-framework-Cocoa>=9.0; sys_platform == 'darwin'` joins the `desktop` extra (NSWorkspace); Windows uses **ctypes only** (no pywin32); X11 uses `xprop` via subprocess.

---

### Task 1 (E4): Frontmost-app adapters

**Files:** create `local_flow/context/__init__.py`, `local_flow/context/frontmost.py`; modify `pyproject.toml` (Cocoa dep); test `tests/test_context.py`.

**Interfaces (exact):**
```python
@dataclass(frozen=True)
class AppInfo:
    app_id: str = ""   # macOS bundle id, Windows exe basename lowercased, X11 WM_CLASS
    title: str = ""    # localized app/window name

class FrontmostAppProvider(ABC):
    @abstractmethod
    def current(self) -> AppInfo: ...  # NEVER raises; empty AppInfo when unknown

class MockFrontmostApp(FrontmostAppProvider):
    def __init__(self, info: AppInfo | None = None): ...
    # .info settable; current() returns it

def create_frontmost_provider() -> FrontmostAppProvider:
    # darwin -> MacFrontmostApp (AppKit.NSWorkspace.sharedWorkspace().frontmostApplication():
    #   bundleIdentifier() or "" , localizedName() or "")
    # win32 -> WindowsFrontmostApp (ctypes user32 GetForegroundWindow ->
    #   GetWindowThreadProcessId -> OpenProcess -> QueryFullProcessImageNameW -> exe basename;
    #   GetWindowTextW for title)
    # linux -> X11FrontmostApp (subprocess xprop -root _NET_ACTIVE_WINDOW then xprop -id <id>
    #   WM_CLASS/_NET_WM_NAME; short timeouts, errors -> empty AppInfo)
    # anything failing at construction or call time degrades to empty AppInfo, never an exception
```
Platform classes import their deps lazily inside `current()`/`__init__`; a failed import makes `current()` return empty `AppInfo` (context awareness is best-effort — this is the one adapter family that must NOT raise, unlike hotkeys). Tests: mock provider behavior; `create_frontmost_provider` returns the right class per monkeypatched `sys.platform` (patch the platform, monkeypatch the class attributes to fakes — same idiom as `tests/test_hotkeys.py::TestFactory`); a provider whose backend raises yields empty `AppInfo`.

- [ ] TDD → commit `feat(context): frontmost-app adapters with best-effort platform backends`

### Task 2 (E4): `app_styles.json` + built-in email/chat styles

**Files:** modify `local_flow/personalization/store.py`; test `tests/test_personalization.py`.

Read the store first and mirror its tolerant-JSON idiom exactly. New file `data_dir/app_styles.json`:
```json
{
  "com.tinyspeck.slackmacgap": "casual",
  "com.apple.mail": {"style": "email", "insert": "paste"},
  "claude": {"insert": "type"}
}
```
**Interfaces:** `@dataclass(frozen=True) class AppRule: style: str = ""; insert: str = ""` and `PersonalizationStore.app_rules() -> dict[str, AppRule]` (keys lowercased; plain-string value = style-only; unknown keys inside dicts ignored). Matching (used by Task 3, implement here as a pure function): `match_app_rule(rules, info: AppInfo) -> AppRule | None` — case-insensitive: exact `app_id` match wins, else first key that is a substring of `app_id` or `title` (sorted by key length desc so most-specific wins).

Built-in styles: wherever the store creates/loads `styles.json` defaults, ensure named styles `email` ("structure as an email: greeting, short paragraphs, sign-off; formal tone") and `chat` ("casual tone, concise, no greeting or sign-off") exist when the file is first created — do not overwrite user-edited files.

- [ ] TDD (plain-string + dict values, matching precedence, tolerant garbage, defaults seeding) → commit `feat(context): per-app style and insert-method rules`

### Task 3 (E4): Per-utterance routing through the pipeline

**Files:** create `local_flow/context/router.py`; modify `local_flow/polish/polisher.py` (`polish(rough, style: str | None = None)` — per-call override, `None` keeps constructor default; ALL existing call sites keep working unchanged), `local_flow/pipeline.py`, `local_flow/app.py` (`_build_pipeline` + `_build_sink` + `_cmd_check` prints detected frontmost app), README (section with the example mapping incl. the Claude-Code/terminal `"insert": "type"` tip for avoiding "[Pasted N lines]"); test `tests/test_context.py`, extend `tests/test_pipeline_integration.py`.

**Interfaces:**
```python
@dataclass(frozen=True)
class ResolvedContext:
    app_id: str = ""
    style: str | None = None   # None -> polisher default
    sink: TextSink | None = None  # None -> pipeline default sink

class ContextRouter:
    def __init__(self, provider: FrontmostAppProvider,
                 rules: dict[str, AppRule],
                 sinks_by_method: Mapping[str, TextSink]) -> None: ...
    def resolve(self) -> ResolvedContext: ...
```
- `DictationPipeline.__init__` gains `router: ContextRouter | None = None`. At the top of `process_transcript`: `ctx = self.router.resolve() if self.router else ResolvedContext()`; polish with `style=ctx.style`; insert via `ctx.sink or self.sink`; `history.append_new(app=ctx.app_id, ...)`.
- `app.py`: `_build_sink` refactors so a `sinks_by_method` dict {"auto","paste","type","clipboard"} of InsertionManagers can be built once (the existing single-sink behavior stays the default); `_build_pipeline` constructs the router with `create_frontmost_provider()` and `store.app_rules()` — router only when `config.context_styles` (new Config field, bool, default True) and rules file non-empty.
- The provider is consulted once per utterance (when recording stopped), not per insert.

Tests: with `MockFrontmostApp(AppInfo("com.tinyspeck.slackmacgap", "Slack"))` + rules mapping slack→casual and a seeded `styles.json` casual style, the MockChatClient prompt contains the casual rules while an unmapped app uses the default style; per-app `insert: "type"` routes to the fake typing sink; `HistoryRecord.app` filled; router=None byte-identical to today.

- [ ] TDD → commit `feat(context): route style, sink, and history app by frontmost app`

### Task 4 (E5): Dictionary store upgrade + per-term usage

**Files:** modify `local_flow/personalization/store.py`, `local_flow/polish/rules.py`, `local_flow/pipeline.py`; test `tests/test_personalization.py`, `tests/test_polish_rules.py`.

- `dictionary.json` supports BOTH legacy entries (plain strings) and rich entries `{"term": "PostgreSQL", "starred": true, "uses": 12}` in the same list. `dictionary_terms() -> list[str]` now orders: starred first, then `uses` desc, then insertion order — all existing callers keep their `list[str]` contract.
- `add_dictionary_term(term: str) -> bool` (False when duplicate): atomic rewrite (tmp file + rename), preserves unknown fields on other entries; **apostrophe-variant dedup**: "Iva's" folds to "Iva" for comparison (strip a trailing `'s`/`’s` before comparing case-insensitively), and adding a variant of an existing term is a no-op returning False.
- `record_term_uses(counts: dict[str, int]) -> None`: increments `uses` per term (creating rich entries from legacy strings as needed), atomic write, silently ignores unknown terms.
- `rules.py`: add `enforce_dictionary_detailed(text, terms) -> tuple[str, dict[str, int]]` (per-term substitution counts); existing `enforce_dictionary` becomes a thin wrapper returning `(text, sum(counts.values()))` so its call sites are untouched.
- `pipeline.py`: `process_transcript` switches to the detailed variant and, when any counts are non-zero and a store is present, calls `record_term_uses` after insertion.

- [ ] TDD (legacy+rich mixed file, ordering, dedup incl. apostrophes, atomicity is best-effort—assert content not fs internals, uses increment through a pipeline run) → commit `feat(personalization): starred terms, usage ranking, apostrophe-safe dictionary adds`

### Task 5 (E5): `suggest_terms` miner + `local-flow learn` + spoken add

**Files:** create `local_flow/personalization/learn.py`; modify `local_flow/app.py` (subcommand `learn`), `local_flow/polish/rules.py` + `local_flow/pipeline.py` (spoken command), README; test `tests/test_learning.py`.

**Interfaces:**
```python
@dataclass(frozen=True)
class Suggestion:
    term: str
    count: int
    sample: str  # one containing sentence fragment (<=80 chars)

def suggest_terms(records: Iterable[HistoryRecord], known: Iterable[str],
                  min_count: int = 3, limit: int = 20) -> list[Suggestion]:
```
Heuristics: tokenize `final` texts; candidates are CamelCase tokens (`JiSpr`, `PostgreSQL`), ALL-CAPS len≥2 (`API`), dotted names (`config.py`), or Capitalized words that are NOT sentence-initial; drop candidates in `known` (apostrophe-folded, case-insensitive) or in a small built-in English stopword set (~50 words: I, The, This, Monday…, plus common sentence starters); count case-insensitively but suggest the most frequent original casing; sort by count desc.

- CLI: `local-flow learn` prints numbered suggestions (`1. Kubernetes (x4) — "…deploy it on Kubernetes tomorrow…"`), `--add N [N…]` adds those numbers, `--add-all` adds everything shown, `--min-count`, `--limit`. Uses `_build_history_store(config)` for records and the store's `add_dictionary_term`. Friendly empty states (no history / no suggestions).
- Spoken command (works with LM Studio down — pure rules): `extract_dictionary_additions(text) -> tuple[str, list[str]]` in `rules.py` matching `add <term> to (the )?dictionary` case-insensitively where `<term>` is 1–4 words; the phrase is removed from the text (with clean whitespace repair). Pipeline calls it right after `apply_dictation_commands`, invokes `store.add_dictionary_term` per term, and appends a warning-style notice `added 'X' to dictionary` to the result warnings so the CLI prints it.
- README: "Teach it your words" section covering learn, --add, and the spoken command.

- [ ] TDD (miner table-driven: proper nouns, CamelCase, dotted, stopwords, sentence-initial exclusion, known exclusion incl. apostrophe variant, casing pick; CLI add flow; spoken command extraction + pipeline wiring with LM Studio mock absent) → commit `feat(learn): history mining, learn CLI, and spoken dictionary additions`

## Self-Review (done)

Seams: `AppRule`/`match_app_rule` produced in T2, consumed by T3's router; `polish(style=)` override keeps all existing call sites; `enforce_dictionary` wrapper keeps E2's tuple contract; `HistoryRecord.app` (E2 field) finally populated in T3; T5's miner consumes `HistoryStore` records via the existing `all()`. Nothing here touches hotkeys or ASR.
