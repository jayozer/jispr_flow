# Phase 6: E8 Transforms + Voice Command Mode, E14 Personal Insights

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Three tasks in order (T1, T2 = E8; T3 = E14). T2 depends on T1.

**Goal:** Wispr's flagship Pro feature — highlight text anywhere, press a hotkey, get an AI rewrite in place, with custom transforms and an optional auto-transform after every dictation — plus voice-driven command mode (hold a second hotkey and *speak* the edit instruction). And `local-flow stats`, a purely local insights report.

## Global Constraints

Standard set (local-only, lazy platform imports, Config/env/TOML validation with hints, mocks + headless tests for every decision path, suite+ruff green per commit, ≤100 cols, README/env/toml per new key). Selection capture and hotkey glue are desktop-extra territory (pynput/pyperclip) — logic classes stay pure.

---

### Task 1 (E8): Selection capture + transforms registry + `local-flow transform`

**Files:** create `local_flow/transforms/__init__.py`, `local_flow/transforms/selection.py`, `local_flow/transforms/registry.py`; modify `local_flow/personalization/store.py` (own `transforms.json` alongside the other JSON files — same tolerant idiom), `local_flow/app.py` (subcommand `transform`), README; tests `tests/test_transforms.py`.

**Interfaces (exact):**
```python
# selection.py
class SelectionBackend(ABC):          # the OS-touching part, injectable
    @abstractmethod
    def read_clipboard(self) -> str: ...
    @abstractmethod
    def write_clipboard(self, text: str) -> None: ...
    @abstractmethod
    def send_copy(self) -> None: ...  # synthesize Cmd+C / Ctrl+C
    @abstractmethod
    def send_paste(self) -> None: ... # synthesize Cmd+V / Ctrl+V

class PynputSelectionBackend(SelectionBackend): ...  # lazy pynput+pyperclip; platform chord
class MockSelectionBackend(SelectionBackend): ...    # scripted clipboard + event log

class SelectionCapture:
    def __init__(self, backend: SelectionBackend,
                 poll_timeout_s: float = 0.4, poll_interval_s: float = 0.02,
                 sleep: Callable[[float], None] = time.sleep) -> None: ...
    def capture(self) -> str:
        # save clipboard -> clear it (write "") -> send_copy -> poll until clipboard
        # changes from "" or timeout -> selection = clipboard (may be "" = no selection)
        # -> REMEMBER saved clipboard for restore. Returns "" when nothing selected.
    def replace(self, text: str) -> None:
        # write text to clipboard -> send_paste -> restore the saved clipboard AFTER
        # a short settle sleep (paste is async; document the race + why the delay).
```
- `PersonalizationStore.transforms() -> dict[str, str]` (name → prompt, ordered) from `data_dir/transforms.json`; file seeded on first store creation with built-ins **Polish** ("Rewrite the text for clarity and concision. Preserve meaning and tone. Return only the rewritten text.") and **Prompt Engineer** ("Restructure the text into a well-formed AI prompt: state the goal, context, constraints, and desired output format. Return only the prompt."); user edits preserved (seed only when file absent, like styles).
- `registry.py`: `apply_transform(chat_client, prompt: str, text: str) -> str` building messages (system = the transform prompt + "Return ONLY the transformed text.", user = text) — reuse the LMStudioClient chat call convention from `polish/polisher.py`; LLM failure raises the existing LMStudio error types (caller handles).
- CLI: `local-flow transform <name> --text TEXT` (headless) and `local-flow transform <name> --selection` (captures via the real backend); `--list` prints available transforms; unknown name → LocalFlowError listing names. `--text` prints the result; `--selection` replaces in place and prints a confirmation to stderr.

**Tests:** SelectionCapture state machine with MockSelectionBackend + fake sleep (capture happy path incl. clipboard restore; nothing-selected timeout → ""; replace ordering: write→paste→restore); transforms.json seeding/tolerance/user-edit preservation; apply_transform via MockChatClient (system prompt content pinned); CLI --text/--list/unknown-name; --selection with a monkeypatched backend.

- [ ] TDD → commit `feat(transforms): selection capture, transform registry, transform CLI`

### Task 2 (E8): Hotkeys — transform-in-place + voice command mode + auto-transform

**Files:** modify `local_flow/config.py` (`transform_hotkey: str = ""` — empty disables; `transform_default: str = "Polish"`; `command_hotkey: str = ""` — empty disables; `auto_transform: str = ""`), `local_flow/hotkeys/base.py` (`TapListener(HotkeyListener-like)`: fires a callback on key TAP — reuse resolve_key; pynput listener on_press only, injected-guarded), `local_flow/app.py` (`_run_loop`: when `transform_hotkey` set, run a TapListener on a daemon thread whose dispatcher-wrapped callback does capture→apply_transform(transform_default)→replace, with warnings to reporter on empty selection or LLM failure — never crash the loop; when `command_hotkey` set, run a second `create_hotkey_listener`-style PushToTalk (PynputPushToTalk with that key) whose finish routes the recorded audio: transcribe → instruction text → `CommandMode.run(instruction, target_text=selection-if-any else last transcript)` → `SelectionCapture.replace` when a selection was captured else `pipeline.sink.insert`; reuse the pipeline's CommandMode instance), `local_flow/pipeline.py` (`auto_transform`: when set and a chat client exists, apply the named transform to the final text right before insertion — failure degrades with a warning, never blocks insertion; skipped at cleanup_level none), README/env/toml; tests: TapListener logic (fake events), auto_transform pipeline test (MockChatClient; ordering: after personalization, before insertion; failure degrades), command-mode routing logic extracted as a testable function `_run_voice_command(deps, pcm, sample_rate, capture) -> None`.

Key constraints: all three features DISABLED by default (empty hotkey/auto_transform = zero behavior change — pin with tests); every OS-touching callback dispatcher-wrapped; capture() on the dispatcher worker (it sends chords + polls — never on the hook thread); document the known limitation that transform/command hotkeys are plain keys (no chords) like the PTT key.

- [ ] TDD → commit `feat(transforms): transform and voice-command hotkeys with auto-transform`

### Task 3 (E14): `local-flow stats`

**Files:** create `local_flow/insights/__init__.py`, `local_flow/insights/stats.py`; modify `local_flow/app.py` (subcommand `stats [--since 30d|7d|all]`), README; tests `tests/test_insights.py`.

**Interfaces:**
```python
@dataclass(frozen=True)
class Stats:
    total_dictations: int
    total_words: int            # words in final texts
    words_per_minute: float     # total_words / (sum(duration_s)/60), 0.0 when no duration
    cleaned_words_delta: int    # sum(max(0, len(rough words) - len(final words)))
    replacements: int           # sum of HistoryRecord.replacements — label honestly:
                                # "smart replacements applied" (substitution count, not corrections)
    failed: int
    top_apps: list[tuple[str, int]]   # top 5 by dictation count; "" -> "(unknown)"
    active_days: list[str]      # ISO dates with >=1 dictation, for the heatmap
    current_streak: int
    longest_streak: int

def compute_stats(records: Iterable[HistoryRecord], now: datetime) -> Stats: ...
def render_heatmap(active_days: list[str], now: datetime, weeks: int = 8) -> str:
    # ASCII: one row per weekday, one column per week, '#' active / '.' inactive
```
- `--since` parses `Nd` / `all` (default `30d`); filtering by record timestamp (tolerant of unparseable → excluded). `now` injected in code, real `datetime.now(UTC)` only at the CLI boundary.
- Output: aligned plain-text report + the heatmap + a one-line note when history is disabled or empty (friendly zero state).

**Tests:** compute_stats table-driven (all fields incl. wpm-without-durations, streak math across gaps, timezone-tolerant timestamps, unknown apps bucket); render_heatmap deterministic snapshot; CLI with seeded store (`--since` filter honored; empty state).

- [ ] TDD → commit `feat(insights): local-flow stats with streak heatmap`

## Manual checklist

1. Select text in any app → press the transform hotkey → text rewritten in place, clipboard restored.
2. `local-flow transform "Prompt Engineer" --text "make the tests faster"` prints a structured prompt.
3. Hold the command hotkey, say "make this more formal" with text selected → selection replaced.
4. `LOCAL_FLOW_AUTO_TRANSFORM=Polish` → every dictation lands pre-polished by the transform.
5. `local-flow stats` shows totals and the streak heatmap.

## Self-Review (done)

T2 reuses T1's capture/registry and the existing CommandMode/dispatcher/reporter seams; empty-config defaults pin zero behavior change; E14 consumes only E2/E4-era HistoryRecord fields with the honest `replacements` label carried from the Phase-2 review note. Selection capture is the one OS-heavy piece and is fully seam-isolated behind SelectionBackend.
