# Phase 3: E6 Tray App + Setup Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Tasks in order T1→T4. T1 is a pure refactor and must land as its own commit with zero behavior change.

**Goal:** A menu-bar/tray app with live recording states, style/language quick-switch menus, and desktop notifications (`local-flow tray`), plus a terminal onboarding wizard (`local-flow setup`) that writes a validated config.

**Architecture:** A `StatusReporter` seam decouples the run loop from its output; the tray is a `TrayReporter` + pystray glue over the same loop. All decision logic (state machine, icon selection, wizard flow) is pure and headless-tested; pystray/Pillow live in a new optional `tray` extra and are imported lazily. GUI appearance itself is manual-verification only.

## Global Constraints

Standard set (local-only, lazy platform imports, Config/env/TOML pattern, `LocalFlowError` + hint, suite+ruff green per commit, ≤100 cols). Additions:
- New optional extra: `tray = ["pystray>=0.19", "pillow>=10.0"]` — NEVER core deps. Tests touching PIL use `pytest.importorskip("PIL")`; tests touching pystray are not written (glue is manual-verify).
- The wizard must not require any extras to run; it *probes* them and reports.
- Live tray behaviors (icon turns red while recording, menu switches styles, notifications appear) are reported as manual checklist items, never claimed verified.

---

### Task 1: `StatusReporter` seam (pure refactor — zero behavior change)

**Files:** create `local_flow/status.py`; modify `local_flow/app.py` (`_cmd_run` and its `handle` closure); test `tests/test_status.py`.

**Interfaces (exact):**
```python
State = Literal["idle", "recording", "processing", "inserted", "error", "warning"]

class StatusReporter(ABC):
    @abstractmethod
    def notify(self, state: State, detail: str = "") -> None: ...

class ConsoleReporter(StatusReporter):
    """Reproduces today's CLI output byte-for-byte."""
    def notify(self, state, detail=""):
        # mapping (exact strings from the current _cmd_run/handle code):
        # "warning"  -> print(f"warning: {detail}", file=sys.stderr)
        # "inserted" -> print(f"inserted: {detail}")
        # "error"    -> print(f"error: {detail}", file=sys.stderr)  [only used where _fail printed]
        # "recording"/"processing"/"idle" -> no output today (silent)
```
- `_cmd_run` gains an internal `reporter: StatusReporter` (constructed as `ConsoleReporter()`; a parameter threaded so `_cmd_tray` can inject later — make `_run_loop(config, args_mode, reporter)` a private function `_cmd_run` delegates to).
- Transitions to emit: `recording` on start(), `processing` on finish() before pipeline call, `inserted` with the final text repr on success, `warning` per pipeline warning, `idle` after each utterance completes, plus cancel path prints stay as-is ("dictation discarded" → keep the literal print OR route as `idle` with detail — keep literal print to preserve output).
- CRITICAL: existing stdout/stderr strings must not change — `tests/test_demo_and_cli.py` and manual behavior identical. Only the plumbing moves.

Tests: ConsoleReporter mapping (capsys), a FakeReporter collecting (state, detail) tuples driven through `_run_loop`'s handle path with mocks (reuse pipeline-integration idioms) asserting the transition sequence for one utterance: recording → processing → (warnings…) → inserted → idle.

- [ ] TDD → full suite → commit `refactor(app): status reporter seam for run loop output`

### Task 2: Tray state machine + Pillow icons + `tray` extra

**Files:** create `local_flow/tray/__init__.py`, `local_flow/tray/state.py`, `local_flow/tray/icons.py`; modify `pyproject.toml` (+`uv sync --extra tray`, commit uv.lock); test `tests/test_tray_state.py`.

**Interfaces:**
```python
# state.py (pure, no Pillow/pystray)
@dataclass(frozen=True)
class TrayView:
    icon: str        # "idle" | "recording" | "processing" | "error"
    tooltip: str     # e.g. "local-flow — recording"
    flash: bool = False  # transient states (inserted) that revert to idle

class TrayStateMachine:
    def apply(self, state: State, detail: str = "") -> TrayView: ...
    # mapping: recording->recording; processing->processing; inserted->idle view with
    # flash=True and tooltip "inserted: <first 40 chars>"; error/warning->error with detail
    # tooltip (warning keeps last non-error icon after one view — keep it simple:
    # warning shows error icon once, next apply() wins); idle->idle.

# icons.py (Pillow only, lazy import inside functions)
def draw_icon(kind: str, size: int = 64) -> "PIL.Image.Image":
    # filled circle on transparent bg: idle=#8a8a8a, recording=#e5484d,
    # processing=#f5a524, error=#7d0b0b with white "!"; unknown kind -> idle color
```
Tests: state machine mapping table-driven (pure); icons via `pytest.importorskip("PIL")` — size/mode assertions and distinct dominant colors per kind.

- [ ] TDD → commit `feat(tray): tray state machine and generated icons`

### Task 3: `local-flow tray` (pystray glue) + language/style switching

**Files:** create `local_flow/tray/app.py`; modify `local_flow/app.py` (subcommand `tray`; `_run_loop` reuse on a worker thread), `local_flow/asr/faster_whisper_asr.py` + `local_flow/asr/mock.py` (settable `language` property — the whisper model is language-agnostic at transcribe time, so switching just changes the per-call arg), `local_flow/config.py` (`languages: str = ""` — comma-separated ISO codes for the quick-switch menu; empty = menu hidden), README ("Tray app" section + manual checklist items), `.env.example`, `local-flow.example.toml`; test: extend `tests/test_asr_config.py` (language property on mock+real classes' mapping logic), `tests/test_config.py` (languages field).

**Design:**
- `TrayApp(config)` builds: the pipeline machinery via the SAME builders `_cmd_run` uses, a `TrayReporter(StatusReporter)` that feeds `TrayStateMachine` and applies `TrayView` to the pystray `Icon` (icon image + title), and runs `_run_loop` on a daemon thread. pystray/Pillow imported lazily in `TrayApp.__init__` → `HotkeyBackendMissingError`-style `LocalFlowError` with hint `uv sync --extra tray` when missing.
- Menu: `Dictation: Start/Stop` (toggles the loop thread via a threading.Event consumed by hands-free mode; for push-to-talk mode the item shows "listening for hotkey"), `Mode: push-to-talk/hands-free` (radio), `Style` submenu (names from `store.styles()` — check the store's actual accessor — clicking sets the active style used for the NEXT utterance: implement by making the pipeline's polisher default style a mutable attribute set via a small setter), `Language` submenu (codes from `config.languages`, sets `transcriber.language`), `Open data folder` (webbrowser/open via `subprocess` per platform, lazy), `Quit`. *(Amended post-implementation: the earlier `Check setup` menu item is dropped deliberately — `local-flow check`'s multi-line output doesn't fit a notification; the CLI command remains the diagnostic path.)*
- Desktop notifications: `TrayReporter` calls `icon.notify(detail)` for `error` and `warning` states (pystray supports notify on the major backends; wrap in try/except — best-effort).
- Everything on the pystray thread must be cheap; the dictation loop stays on its worker thread; state hand-off via the reporter only (no shared mutable state beyond documented setters).
- Tests: only the pure/headless parts (language property mapping incl. "auto"→None on the real class **constructor-level logic** — instantiate nothing heavy: test the mapping via the mock and via FasterWhisperTranscriber's property setter if constructible without the model — check how the class defers model loading; if the model loads in `__init__`, add the property to both classes and test on the mock only, noting that). `languages` config parsing helper `parse_languages("en, de,tr") -> ["en","de","tr"]` in tray/app.py or config.py — pure, tested.

- [ ] TDD for the testable parts → commit `feat(tray): menu-bar app with live states, style and language switching`

### Task 4: `local-flow setup` wizard

**Files:** create `local_flow/setup_wizard.py`; modify `local_flow/app.py` (subcommand `setup`), README (Quick start leads with `uv run local-flow setup`); test `tests/test_setup_wizard.py`.

**Interfaces:**
```python
def run_wizard(config: Config,
               ask: Callable[[str], str] = input,
               say: Callable[[str], None] = print,
               target: Path | None = None) -> Path:
    # 1. probe extras (import faster_whisper / sounddevice / pynput / pyperclip / pystray
    #    inside try/except) and LM Studio (client.list_models() with short timeout) — report each
    # 2. questions (each with default shown, empty answer = default):
    #    hotkey: fn (macOS only — offer per platform) | space | f9
    #    mode: push-to-talk | hands-free
    #    style: from store's style names (default "default")
    #    asr model: small.en | small (multilingual) | base.en
    #    language: en | auto (only if multilingual model chosen)
    # 3. write TOML to target or ~/.config/local-flow/config.toml (mkdir -p; refuse to
    #    overwrite an existing file without an explicit "y" confirmation)
    # 4. validate by load_config(config_file=written_path) — on ConfigError, delete the
    #    file and re-raise (never leave a broken config behind)
    # 5. print next steps (grant permissions, start LM Studio, run `local-flow run`)
```
- Answer validation: invalid answer → re-ask (loop), max 3 attempts then keep default.
- Cross-checks reuse existing validation: e.g. language=auto forces a multilingual model choice (the wizard simply doesn't offer invalid combos).
- Tests: scripted `ask` sequences (happy path; invalid-then-valid; decline-overwrite keeps existing file; ConfigError path cleans up — force it by monkeypatching load_config), written TOML content assertions, probes mocked.

- [ ] TDD → full suite → commit `feat(setup): interactive onboarding wizard writing validated config`

## Manual checklist (Task 8-style, for the user)

1. `uv sync --all-extras && uv run local-flow tray` → icon appears; turns red while holding the hotkey, amber while processing, notifies on errors.
2. Tray Style submenu → switch to `email` → next dictation uses email structure.
3. Tray Language submenu (with `LOCAL_FLOW_LANGUAGES=en,de` and multilingual model) → switch to `de`, dictate German.
4. `uv run local-flow setup` on a machine without config → answers produce a working `~/.config/local-flow/config.toml`.

## Self-Review (done)

T1's reporter is consumed by T3's TrayReporter; T2's state machine/icons are pure and independently testable; language switching leverages E3's per-call language arg; the wizard reuses `load_config` validation rather than duplicating rules. No epic dependencies beyond shipped phases.
