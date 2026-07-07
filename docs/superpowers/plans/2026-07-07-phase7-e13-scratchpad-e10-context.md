# Phase 7: E13 Scratchpad + E10 Context-Aware Dictation

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Three tasks in order (T1, T2 = E13; T3 = E10). E10 is deliberately last — it is the epic's hardest platform work.

**Goal:** A floating always-on-top notepad backed by plain markdown files, toggled by hotkey, that dictation can land in directly (E13); and best-effort reading of the focused field's existing text so polish continues sentences, matches tone, and reuses nearby name spellings (E10).

## Global Constraints

Standard set (local-only, lazy platform imports with mocks, Config/env/TOML validation + hints, headless tests for every decision path, suite+ruff green per commit, ≤100 cols, README/env/toml per key). GUI (tkinter window) and OS accessibility calls are manual-verify glue; ALL routing/store/prompt logic is pure and tested. E10's field reading follows the E4 frontmost-app precedent: BEST-EFFORT, never raises, degrades to empty.

---

### Task 1 (E13): Note store + scratchpad sink + `local-flow pad` CLI

**Files:** create `local_flow/scratchpad/__init__.py`, `local_flow/scratchpad/store.py`, `local_flow/scratchpad/sink.py`; modify `local_flow/app.py` (subcommand `pad`), README; tests `tests/test_scratchpad.py`.

**Interfaces (exact):**
```python
# store.py
class NoteStore:
    def __init__(self, data_dir: Path, now: Callable[[], datetime] | None = None) -> None:
        # notes live under data_dir / "notes" / "<name>.md"; created lazily on first write
    @property
    def notes_dir(self) -> Path: ...
    def list_notes(self) -> list[str]: ...          # sorted names, no extension
    def active_note(self) -> str: ...               # persisted in notes/.active (plain text);
                                                    # default "inbox" when unset/missing
    def set_active(self, name: str) -> None: ...    # validates name: [A-Za-z0-9._ -]{1,64},
                                                    # no path separators -> LocalFlowError + hint
    def read(self, name: str) -> str: ...           # "" when absent
    def append(self, text: str, name: str | None = None) -> Path:
        # appends to (name or active note); creates dirs/file lazily; each append is
        # "\n\n" + text when the file already has content, else text; returns the path
    def create(self, name: str) -> Path: ...        # empty file; validates name; no overwrite
                                                    # (existing file is fine — idempotent)

# sink.py
class ScratchpadSink(TextSink):
    """TextSink that appends into the note store instead of the desktop."""
    def __init__(self, store: NoteStore) -> None: ...
    def insert(self, text: str) -> None: ...        # store.append(text)
    def press_key(self, key: str) -> None: ...      # "enter" -> append "" (i.e. paragraph
                                                    # break next append); other keys no-op
```
- CLI: `local-flow pad --list`, `--show [NAME]` (active by default), `--append TEXT [--note NAME]`, `--use NAME` (set active, creating if missing), `--new NAME`. All headless. No window in this task.
- README: "Scratchpad" section part 1 (notes are plain markdown you can open anywhere; the window comes in T2).

**Tests:** store round-trips (append spacing rule, lazy creation, active-note persistence across instances, name validation incl. path traversal attempts, list sorting); sink insert/press_key semantics; CLI flows incl. friendly empty states.

- [ ] TDD → commit `feat(scratchpad): markdown note store, scratchpad sink, pad CLI`

### Task 2 (E13): Floating window + hotkey toggle + dictate-to-pad

**Files:** create `local_flow/scratchpad/window.py` (`ScratchpadWindow`: tkinter, lazy import in `__init__` → LocalFlowError with hint "tkinter unavailable; use local-flow pad --show" when missing; always-on-top (`wm_attributes("-topmost", True)`), a Text widget showing the active note, note switcher via a simple OptionMenu, autosave-on-change debounced 1s, refresh-from-disk when an external append happens — poll mtime every 500ms via `after()`); modify `local_flow/config.py` (`scratchpad_hotkey: str = ""` — empty disables; collision-validated with the other hotkeys), `local_flow/app.py` (`_run_loop`: TapListener on scratchpad_hotkey toggling a `pad_active` flag; while active, `_handle_utterance` routes insertion through a `ScratchpadSink` instead of the normal sink — implement by extending `RunDependencies` with `scratchpad_sink: TextSink | None = None` and the toggle flag in a small mutable holder; reporter notifies "scratchpad on/off" via warning-level or a literal print — pick the reporter, detail "dictating to scratchpad: <note>"), README part 2 + manual checklist items; tests: toggle/routing logic pure (fake sink; assert utterances land in the store while active and in the normal sink when not), config collision, window = manual-only.
- The tkinter window runs on its own thread? NO — tkinter must run on a main thread reliably on macOS. Design: `local-flow pad --window` runs the window as the MAIN program (blocking, like tray), with dictation loop optionally started via `--with-dictation` on a worker thread (reuse `_run_loop` + reporter). The scratchpad HOTKEY toggle in `local-flow run` only routes text to the store (window not required — notes are files; the user can have `pad --window` open in another process, which live-refreshes via mtime polling). Document this two-process design honestly in README.

**Tests:** routing toggle behavior end-to-end with fakes (on → store receives text + normal sink untouched; off → reverse; toggle mid-session); config collision validation; `pad --window` construction error path headless (monkeypatch tkinter import failure message).

- [ ] TDD → commit `feat(scratchpad): floating window and dictate-to-pad hotkey`

### Task 3 (E10): Context-aware dictation

**Files:** create `local_flow/context/field_text.py`; modify `local_flow/polish/prompting.py` (context block), `local_flow/polish/polisher.py` (`polish(..., field_context: FieldContext | None = None)`), `local_flow/pipeline.py` (resolve once per utterance alongside the router), `local_flow/app.py` (`_build_pipeline` wiring gated on new config `context_awareness: bool = True` AND desktop backend availability), `local_flow/config.py`, README (privacy note: field text goes ONLY to the local LM Studio server), `.env.example`, `local-flow.example.toml`; tests `tests/test_field_context.py`.

**Interfaces (exact):**
```python
@dataclass(frozen=True)
class FieldContext:
    before_cursor: str = ""   # capped at 1000 chars (tail)
    selected: str = ""

class FieldTextProvider(ABC):
    @abstractmethod
    def current(self) -> FieldContext: ...  # NEVER raises; empty on any failure

class MacAXFieldText(FieldTextProvider): ...
    # AXUIElementCreateSystemWide -> kAXFocusedUIElementAttribute ->
    # kAXValueAttribute + kAXSelectedTextRangeAttribute (pyobjc ApplicationServices —
    # ALREADY a pynput dependency on darwin; no new pyproject dep needed unless import
    # probing shows otherwise — verify and state in the report)
    # before_cursor = value[:selection.location][-1000:]
class WindowsUIAFieldText(FieldTextProvider): ...
    # ctypes/comtypes-free v1: use UIAutomationCore via ctypes is complex — v1 SHIPS A
    # STUB returning empty FieldContext with a docstring explaining why (COM interop
    # needs comtypes, a new dep) — document as a known platform gap in README.
class NullFieldText(FieldTextProvider): ...      # always empty
class MockFieldText(FieldTextProvider): ...      # settable

def create_field_text_provider() -> FieldTextProvider:  # darwin->Mac, else Null (win stub = Null)
```
- Prompt block (all LLM cleanup levels, appended after the level prompt, only when `before_cursor` or `selected` non-empty): "The user is continuing existing text that ends with: <before_cursor tail>. Continue naturally from it: do not repeat it, match its tone and formatting, and reuse the exact spellings of any names or terms appearing in it. Return only the new text." Pinned by tests; ABSENT when context empty (prompt byte-identical to today — pin that too).
- Pipeline: provider consulted once per utterance at the same point as the router (best-effort); `history` unaffected. `context_awareness=false` or provider Null → byte-identical behavior.
- Manual checklist: dictate after "Dear Dr. Adithya," in a note → continuation without re-greeting, name spelled correctly.

**Tests:** provider contract (mock; failure → empty; factory dispatch per platform with monkeypatched classes); prompt block presence/absence + content pin + 1000-char tail cap; pipeline integration with MockFieldText (MockChatClient receives the context block; empty context → prompts byte-identical to pre-E10); config gate.

- [ ] TDD → commit `feat(context): field-text awareness feeds polish continuation`

## Manual checklist

1. `local-flow pad --append "first thought"` then `--show` → note content printed; `pad --window` shows it, stays on top, live-updates when another terminal appends.
2. `LOCAL_FLOW_SCRATCHPAD_HOTKEY=f6 local-flow run` — tap F6, dictate → text lands in the active note, not the editor; tap again → normal insertion resumes.
3. (macOS) Focus a TextEdit doc ending "Dear Dr. Adithya," — dictate "thanks for the referral" → polished continuation, no repeated greeting, name preserved.

## Self-Review (done)

E13's sink implements the existing TextSink contract so routing is a one-field swap; the two-process window design avoids tkinter-thread traps and is documented rather than hidden. E10 mirrors E4's best-effort adapter discipline exactly, ships an honest Windows stub instead of untested COM code, and pins byte-identical behavior when disabled/empty. After T3, run the FINAL whole-branch review (e2193ca..HEAD) per the ledger decision.
