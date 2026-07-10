# Jispr Flow

Local-first, open-source desktop dictation in the spirit of "flow"-style
dictation apps — built from scratch, with **no** proprietary code, branding,
assets, or network services. Speak; a local ASR model transcribes; a local
LM Studio model polishes; the result lands in your active app.

> **Name:** the app is **Jispr Flow**. Its CLI command and Python package are
> `local-flow` and `local_flow` — that's what you type in the examples below
> (e.g. `uv run local-flow run`), and what config filenames, paths, and
> `LOCAL_FLOW_*` environment variables are keyed on.

**Privacy statement:** everything runs on your machine. Audio never leaves
your computer; transcripts are only sent to *your own* LM Studio server
(default `http://localhost:1234/v1`). The client refuses known cloud AI
endpoints (OpenAI, Anthropic, Wispr, etc.) by design. Personal data
(dictionary, snippets, styles) is stored in plain JSON files you own.

## What it does

- **Dictation** — push-to-talk (hold a key) or hands-free (VAD detects when
  you stop speaking), local Whisper transcription, rule cleanup (fillers,
  self-corrections), LM Studio polish, then paste into the active app.
- **Dictation commands** — say "new line", "new paragraph", or end with
  "press enter" to have Enter pressed after insertion.
- **Personal dictionary** — canonical spellings ("PostgreSQL", "JiSpr Flow")
  enforced on every output.
- **Snippets** — spoken trigger phrases ("sig block") expand to stored text.
- **Styles** — named writing-style rules injected into the polish prompt.
- **Command mode** — transform provided/selected text or your last transcript
  with an instruction ("make this formal", "turn it into bullets").
- **Text transforms** — apply a named, reusable AI rewrite ("Polish",
  "Prompt Engineer", or your own) to `--text` or whatever's highlighted in
  the frontmost app.

## Install (uv)

```bash
git clone <this repo> && cd jispr_flow
uv sync --all-extras         # core + dev deps + every optional extra
uv run local-flow setup      # interactive wizard: probes your setup, writes config.toml
uv run local-flow demo       # prove the whole pipeline with mocks, no permissions
```

`local-flow setup` reports which optional dependencies and LM Studio are
reachable, asks a handful of questions (hotkey, capture mode, ASR model,
style), and writes a validated `~/.config/local-flow/config.toml` — it never
overwrites an existing config without asking first. Prefer to configure by
hand instead? Add just the extras you need and skip the wizard:

```bash
uv sync                      # core + dev deps; runs headless demo and tests
uv sync --extra asr          # faster-whisper (local speech-to-text)
uv sync --extra mlx-asr      # MLX Whisper (Apple-Silicon-native, opt-in)
uv sync --extra audio        # sounddevice (mic) + webrtcvad
uv sync --extra desktop      # pynput (hotkeys/paste) + pyperclip (clipboard)
uv sync --extra tray         # pystray + pillow (menu-bar app)
```

Entry points: `uv run local-flow` or `uv run python -m local_flow`.

## LM Studio setup

1. Install [LM Studio](https://lmstudio.ai) (free, runs fully locally).
2. Download an instruct model. Good CPU/GPU-friendly picks:
   - **Qwen2.5 7B Instruct** (best quality/speed balance)
   - **Llama 3.1 8B Instruct**
   - **Qwen2.5 3B Instruct** or **Phi-3.5 mini** on weaker hardware
3. Load the model, open the **Developer** tab, and **Start Server**
   (default `http://localhost:1234`).
4. Optionally set `LOCAL_FLOW_LMSTUDIO_MODEL`; left empty, Jispr Flow
   auto-picks the first loaded model.

LM Studio is used **only** for text polish and command mode — never for
speech recognition. ASR is a separate local adapter.

## ASR backends and recommended models

`faster-whisper` remains the repository default and most portable backend.
On the evaluated M5 Max, `mlx-whisper` is the fastest tested option:

| Backend / model | Aggregate median latency | Vocabulary-aware WER |
|---|---:|---:|
| MLX Whisper `whisper-small.en-mlx` | `0.093s` | `0.034` |
| faster-whisper `small.en` | `0.862s` | `0.034` |

MLX was about **89% faster** on that machine with identical controlled WER.
This result is specific to the tested Apple Silicon hardware, models, and
three-sample corpus; it is not a universal ranking across every computer or
model. The live microphone test also completed without duplicate insertions
and correctly recognized the boosted technical vocabulary.

Install MLX support, then make it the local default in `.env`:

```bash
uv sync --extra mlx-asr
```

```dotenv
LOCAL_FLOW_ASR_BACKEND=mlx-whisper
LOCAL_FLOW_ASR_MODEL=mlx-community/whisper-small.en-mlx
LOCAL_FLOW_ASR_LANGUAGE=en
```

Run with `uv run local-flow run`. To switch back, change only the backend and
model:

```dotenv
LOCAL_FLOW_ASR_BACKEND=faster-whisper
LOCAL_FLOW_ASR_MODEL=small.en
```

MLX model names are local directories or Hugging Face repos containing
MLX-converted Whisper weights. `asr_device` and `asr_compute_type` describe
the faster-whisper configuration; MLX model repositories encode their own
precision/quantization. See [the MLX evaluation](docs/asr/MLX_EVALUATION.md)
for the acceptance criteria and measured comparison.

Recommended faster-whisper models:

| Model            | Speed  | Quality | Notes                                    |
|------------------|--------|---------|-------------------------------------------|
| `base.en`        | fast   | okay    | quick notes (English only)                |
| `small.en`       | medium | good    | recommended default (English only)        |
| `medium.en`      | slow   | better  | if you have a GPU (English only)          |
| `small`          | medium | good    | multilingual; recommended for `auto`      |
| `medium`         | slow   | better  | multilingual, better quality, slower      |
| `large-v3-turbo` | slower | best    | multilingual, needs decent hardware       |

`.en` models are English-only and cannot be combined with a non-English
`LOCAL_FLOW_ASR_LANGUAGE` (see below). Model names are downloaded once into
a local cache; you can also point `LOCAL_FLOW_ASR_MODEL` at a directory
containing a CTranslate2 model for a fully offline install.

Set `LOCAL_FLOW_ASR_LANGUAGE` to control speech recognition language:
`en` (default), any ISO 639-1 code (e.g. `fr`, `de`, `es`), or `auto` to
detect the spoken language per utterance. `auto` and non-`en` codes require
a multilingual model (e.g. `small`, not `small.en`).

## Configure

Copy `.env.example` to `.env` (or export the variables, or write
`local-flow.toml` — see `local-flow.example.toml`). Precedence:
environment > config file > defaults. Highlights:

| Setting | Env var | Default |
|---|---|---|
| LM Studio URL | `LOCAL_FLOW_LMSTUDIO_BASE_URL` | `http://localhost:1234/v1` |
| LM Studio model | `LOCAL_FLOW_LMSTUDIO_MODEL` | *(auto-pick)* |
| ASR backend | `LOCAL_FLOW_ASR_BACKEND` | `faster-whisper` |
| ASR model | `LOCAL_FLOW_ASR_MODEL` | `small.en` |
| ASR language | `LOCAL_FLOW_ASR_LANGUAGE` | `en` (or an ISO code, or `auto`) |
| Tray languages | `LOCAL_FLOW_LANGUAGES` | *(empty; comma-separated codes)* |
| VAD backend | `LOCAL_FLOW_VAD_BACKEND` | `energy` (or `webrtc`) |
| VAD preset | `LOCAL_FLOW_VAD_PRESET` | `normal` (or `whisper`; see "Microphone priority & whisper mode") |
| Mic priority | `LOCAL_FLOW_MIC_PRIORITY` | *(empty; comma-separated device-name substrings)* |
| Max utterance length | `LOCAL_FLOW_MAX_UTTERANCE_MIN` | `20` (minutes; warns, does not truncate) |
| Mode | `LOCAL_FLOW_MODE` | `push-to-talk` (or `hands-free`) |
| Hotkey | `LOCAL_FLOW_HOTKEY` | fn (macOS) / f9 |
| Hotkey hold threshold | `LOCAL_FLOW_HOTKEY_SPACE_HOLD_MS` | `250` |
| Cancel hotkey | `LOCAL_FLOW_CANCEL_HOTKEY` | `esc` |
| Mouse button | `LOCAL_FLOW_MOUSE_BUTTON` | *(empty; or `middle`/`x1`/`x2`; see "Mouse push-to-talk")* |
| Mouse mode | `LOCAL_FLOW_MOUSE_MODE` | `hold` (or `toggle`) |
| Mouse Enter button | `LOCAL_FLOW_MOUSE_ENTER_BUTTON` | *(empty; or `middle`/`x1`/`x2`)* |
| Style | `LOCAL_FLOW_STYLE` | `default` |
| Cleanup level | `LOCAL_FLOW_CLEANUP_LEVEL` | `medium` (or `none`/`light`/`high`; see "Cleanup levels") |
| Data dir | `LOCAL_FLOW_DATA_DIR` | `~/.local/share/local-flow` |
| Streaming | `LOCAL_FLOW_STREAMING` | `off` (or `sentence`/`live-preview`, hands-free only; see "Streaming") |
| Streaming pause | `LOCAL_FLOW_STREAMING_PAUSE_MS` | `300` |
| Context-aware dictation | `LOCAL_FLOW_CONTEXT_AWARENESS` | `true` (see "Context-aware dictation") |

Personalization lives in the data dir as hand-editable JSON:
`dictionary.json` (canonical terms), `snippets.json` (trigger → expansion),
`styles.json` (named style rules + the active one).

Dictionary terms also bias Whisper before decoding. Jispr Flow builds a
bounded `initial_prompt` from the current priority order (starred terms,
then frequently used terms), refreshes it before every live or file
transcription, and collapses blank/duplicate entries. A term added through
learning or the spoken dictionary command therefore reaches the next
utterance without restarting. Post-transcription dictionary enforcement
still runs as a second safety net for canonical casing.

## Per-app styles & insertion

Jispr Flow can look at the frontmost app/window when a dictation finishes and
apply a different polish style and/or insertion method for it. Add
`app_styles.json` to your data dir — it's the only personalization file that
is *not* auto-created, so it simply does nothing until you add one:

```json
{
  "com.tinyspeck.slackmacgap": "casual",
  "com.apple.mail": {"style": "email", "insert": "paste"},
  "claude": {"insert": "type"}
}
```

Keys are matched case-insensitively against the frontmost app's bundle
id/executable/`WM_CLASS` and its window title: an exact match on the app id
wins outright, otherwise the longest key that appears as a substring of
either wins (so `"slackmacgap"` beats a plainer `"slack"`). A plain string
value sets only the style (e.g. `"casual"`, matching a name in
`styles.json`, which also ships built-in `email` and `chat` styles); a
`{"style": ..., "insert": ...}` object can also override the insertion method
for that app (`auto` | `paste` | `type` | `clipboard`).

The `"claude": {"insert": "type"}` entry is a tip for terminal apps
(including Claude Code): the default `paste` keystroke lands as a giant
clipboard blob that many terminals render as `[Pasted N lines]` instead of
real text, so routing terminal/Claude-Code windows to `"insert": "type"`
(synthetic keystrokes) types the text in directly instead.

Set `LOCAL_FLOW_CONTEXT_STYLES=false` to disable frontmost-app lookups
entirely (e.g. if you don't want Jispr Flow querying the active window).

## Use

```bash
uv run local-flow setup      # interactive onboarding wizard; writes config.toml
uv run local-flow check      # diagnose LM Studio / ASR / audio / clipboard
uv run local-flow run        # live dictation (hold Fn/Space, speak, release)
uv run local-flow run --mode hands-free   # VAD-segmented, no hotkey needed
uv run local-flow recover    # reprocess any dictation audio a crash left behind
uv run local-flow polish "um send the uh draft, scratch that, the final doc"
uv run local-flow transcribe memo.m4a --polish   # audio file -> polished notes
uv run local-flow command "make this formal" --text "hey can u fix the bug"
uv run local-flow transform --list                       # show available named transforms
uv run local-flow transform Polish --text "hey can u fix the bug pls"
uv run local-flow transform Polish --selection            # transform the current OS selection
uv run local-flow demo       # headless end-to-end proof with mocks
uv run local-flow history                 # list recent dictations, newest first
uv run local-flow history --search invoice --limit 5
uv run local-flow history --verbose       # also show the rough (pre-polish) transcript
uv run local-flow history --clear         # delete the local history file
uv run local-flow history --show 1        # print record #1's full rough + final text
uv run local-flow history --reinsert-raw 1   # undo a bad AI edit: re-insert record #1's rough text
uv run local-flow history --retry 1       # redo polish+insert for record #1 (fresh LLM call)
uv run local-flow learn                   # mine history for candidate dictionary terms
uv run local-flow learn --add 1 2         # add suggestions #1 and #2 to the dictionary
uv run local-flow stats                   # local-only insights: words, streaks, top apps
uv run local-flow stats --since all       # same, over your entire history
uv run local-flow tray                    # menu-bar app (see "Tray app" below)
uv run local-flow pad --append "call the plumber"   # jot a note (active note by default)
uv run local-flow pad --show                        # print the active note
uv run local-flow pad --list                         # list all notes (active one marked)
uv run local-flow pad --use work                     # switch (creating if missing) the active note
```

### History & privacy

Every completed dictation (rough transcript, polished final, whether LM Studio
was used, duration, replacement count) is appended as one JSON line to a local
file: `<data dir>/history.jsonl` (e.g. `~/.local/share/local-flow/history.jsonl`).
It never leaves your machine and is plain, hand-editable text.

- Disable recording entirely with `LOCAL_FLOW_HISTORY_ENABLED=false`.
- Control how long entries are kept with `LOCAL_FLOW_HISTORY_RETENTION`:
  `forever` (default), `24h` (prune anything older on each write), or `off`
  (never write).
- `LOCAL_FLOW_HISTORY_MAX_ENTRIES` caps the file size by rotating out the
  oldest entries beyond that count (default `5000`).
- `uv run local-flow history --clear` deletes the file immediately.

**Undo a bad AI edit** — `N` in `--show N`/`--reinsert-raw N` is always
1-based against the plain, unfiltered `local-flow history` listing (newest
first); `--search`/`--limit` given alongside them are ignored, so the number
you see in a plain listing always means the same record.

- `local-flow history --show N` prints record `N`'s full rough (pre-polish)
  and final (post-polish) text, untruncated — useful for seeing exactly what
  the polish pass changed.
- `local-flow history --reinsert-raw N` re-inserts record `N`'s rough
  transcript, verbatim, through your configured insertion method — an undo
  for when the AI polish mangled something: dictate again over the bad
  result, or paste the raw words back in yourself and fix them by hand.
- `local-flow history --retry N` re-runs record `N`'s rough transcript
  through a *freshly built* pipeline — a fresh LM Studio polish pass and a
  fresh insert — for when the first attempt's polish failed (LM Studio was
  down, or a record's `failed` flag is set; see below). This appends a
  **new** history record rather than replacing the old one, so both attempts
  stay in your history.
- Out-of-range `N` fails with a friendly error naming how many records
  exist, instead of a traceback.

Every record also carries a `failed` flag: true when LM Studio was
configured but never actually contributed to that dictation's polish (down,
timed out, or otherwise skipped) — except at `LOCAL_FLOW_CLEANUP_LEVEL=none`,
where the LLM is never called by design and skipping it isn't a failure.
`--retry` is the fix for a `failed` record once LM Studio is back up.

### Scratchpad

`local-flow pad` gives dictation somewhere to land that isn't any particular
app: a set of plain markdown files under `<data dir>/notes/` (e.g.
`~/.local/share/local-flow/notes/`), one file per note (`<name>.md`), that
you can open, edit, sync, or grep with any tool you like -- there is no
proprietary format and nothing here ever leaves your machine. One note is
always "active" (its name is remembered in `notes/.active`, `inbox` by
default); most `pad` commands operate on the active note unless told
otherwise.

```bash
uv run local-flow pad --list                    # list note names (active one marked)
uv run local-flow pad --show                    # print the active note (default action)
uv run local-flow pad --show meetings           # print a specific note
uv run local-flow pad --append "buy milk"       # append to the active note
uv run local-flow pad --append "agenda item" --note meetings   # append to a different note
uv run local-flow pad --use meetings            # switch the active note (creates it if missing)
uv run local-flow pad --new someday             # create an empty note without switching to it
```

Each `--append` starts a new paragraph: if the target note already has
content, the new text is separated from it by a blank line, so successive
jottings read as distinct entries rather than run-on text. Note names may
only contain letters, digits, spaces, `.`, `_`, `-` (1-64 characters), may
never contain a path separator like `/` or `..`, and may not end in `.` or
` ` (accepted characters elsewhere in the name, but Windows-hostile as a
trailing character) -- so a note name can never escape the notes directory
and stays portable across platforms. There's no cross-process locking on
appends (this is a single-user, local-first tool by design): two processes
racing to make the very first append to the same empty note at the same
instant could both see it as empty and skip the blank-line separator
between them, but a single writer -- the normal case -- is unaffected.

#### Floating window

`local-flow pad --window` opens `ScratchpadWindow`: a small, always-on-top
editor over the active note (an `OptionMenu` to switch notes, a `Text`
widget for the content). It requires a Tk-enabled Python (`import tkinter`
must work -- `brew install python-tk@3.12` on macOS, `python3-tk` on
Debian/Ubuntu; a missing tkinter fails with a hint instead of a traceback).
Edits you type autosave to disk after a 1-second pause (debounced -- typing
continuously keeps pushing the save back, so it never writes on every
keystroke), and the window polls the active note's file every 500ms to pick
up changes made elsewhere (another terminal's `pad --append`, or the
dictate-to-pad hotkey below) -- except while you have unsaved edits in the
window, when that external-refresh poll is skipped so it can never clobber
what you're mid-typing. The reverse race is guarded too: if an external
write lands on disk while you have unsaved edits, the next autosave notices
the note's mtime moved and refuses to overwrite it, instead flipping the
window title to a conflict notice and refusing to switch notes until you
copy your text out, then reload (e.g. restart the window) -- so neither
side's text is ever silently destroyed.

**Two-process design, on purpose:** `--window` runs as its own blocking main
program (like `local-flow tray`'s menu-bar icon) rather than as a thread
inside `local-flow run` -- tkinter needs a single main thread to behave
reliably, especially on macOS, and `local-flow run` already has its own main
thread doing hotkey/audio work. The window and `local-flow run` (in a
separate terminal) stay in sync purely through the note files on disk --
no socket, no IPC, nothing new to configure. If you'd rather not juggle two
terminals, `--with-dictation` starts the normal dictation loop on a worker
thread for the lifetime of the window (one process, one terminal):

```bash
uv run local-flow pad --window                    # just the window
uv run local-flow pad --window --with-dictation   # window + dictation, one process
```

#### Dictate to scratchpad

`LOCAL_FLOW_SCRATCHPAD_HOTKEY` (push-to-talk mode only, like the transform/
command hotkeys) turns any tap into a toggle: while active, every dictated
utterance's insertion is routed into the scratchpad's active note instead of
the frontmost app -- tap again to resume normal insertion. Empty (the
default) disables the feature entirely.

```bash
LOCAL_FLOW_SCRATCHPAD_HOTKEY=f8 uv run local-flow run   # tap F8 to start/stop
                                                          # dictating into the pad
```

While active, this wins over *everything else* that would otherwise decide
where text lands, including a per-app insert override from `app_styles.json`
(see "Per-app styles & insertion") -- the scratchpad is a deliberate,
explicit override the user just asked for, so it takes priority over an
automatic per-app rule. History still records normally (including which app
was frontmost) -- only where the text is *inserted* changes. The toggle
notification is reported the same way other hotkey warnings are (see
"Transform anywhere"): printed to the console and surfaced as a tray
notification when running under `local-flow tray`.

This is a routing-only feature -- the hotkey works whether or not a
`pad --window` is even open in another terminal; notes are just files, and
appending to one doesn't require a window to be watching it. Open the window
if you want to see the words land live.

### Personal insights (`local-flow stats`)

`local-flow stats` turns your local `history.jsonl` into a purely local
report -- nothing here ever leaves your machine, and no history means an
empty (friendly) report, not an error.

```bash
uv run local-flow stats               # last 30 days (the default)
uv run local-flow stats --since 7d    # last 7 days
uv run local-flow stats --since all   # every record ever stored
```

```
local-flow stats -- since 30d
  total dictations           : 42
  total words                : 3108
  words per minute           : 118.4
  cleaned words              : 96
  smart replacements applied : 12
  failed (LM Studio skipped) : 0
  top apps                   : Slack (18), VS Code (11), (unknown) (5)
  current streak             : 4 day(s)
  longest streak             : 9 day(s)

last 8 weeks:
Mon ......##
Tue ......##
Wed ......#.
...
```

Notes on the numbers:

- **words per minute** is `total words / (total recorded duration / 60)`;
  `0.0` when no record in the window has a nonzero duration.
- **cleaned words** is how many words shorter your final text is than the
  rough transcript, summed per record and never negative per record (a
  record where the final text got *longer* contributes `0`, not a penalty).
- **smart replacements applied** is honestly labeled: it's a count of
  substitutions your dictionary/rules performed, including ones where the
  replacement text was already correct -- it is **not** a count of words
  that were wrong and got fixed. Read it as "how much personalization fired,"
  not "how many mistakes were caught."
- **top apps** shows the 5 most-dictated-into apps by count; an empty app
  name (context styles off, or an app that didn't report one) is bucketed as
  `(unknown)`.
- **current streak** and **longest streak** count consecutive active days
  within the records the current `--since` window includes; both are
  measured from "today or yesterday back" (current) or across any historical
  gap (longest). Pass `--since all` for true all-time streaks, since a
  narrower window naturally can't see runs before it. Current streak counts
  consecutive active days ending today OR yesterday -- dictating yesterday
  but not yet today still shows a live streak; it only resets to `0` once a
  full calendar day passes with zero dictations (today *and* yesterday both
  empty).
- **failed (LM Studio skipped)** is informational: failed records still count
  in total dictations, total words, and WPM, since their rule-cleaned text
  was inserted; the failed field shows how many times LLM polish was skipped.
- A record whose timestamp can't be parsed is excluded from the report
  entirely (every field, not just the ones that need a date) rather than
  being arbitrarily counted in one window or another; if any were skipped
  this way, a one-line note says how many.
- The heatmap under "last 8 weeks" is a plain-text activity grid: one row
  per weekday (`Mon`..`Sun`), one column per week (oldest first, this week
  last), `#` for a day with >=1 dictation and `.` otherwise.

### Crash-safe audio recovery

Before an utterance's audio is handed to the pipeline, its raw PCM is saved
as a WAV file under `<data dir>/pending/` (a uuid-named file, no clock
dependency); the file is deleted the moment that utterance finishes
processing successfully. If Jispr Flow crashes, is force-quit, or the
insertion step fails partway through, the WAV is left behind instead of the
dictation being silently lost.

```bash
uv run local-flow recover   # reprocesses every WAV under <data dir>/pending/
```

Each pending file is run back through the same ASR/polish/insert pipeline
`local-flow run` uses: on success it is transcribed, polished, inserted, and
deleted; on failure (e.g. LM Studio still down, insertion still failing) it
is left in place so a later `recover` can try again. A `recover` with
nothing pending prints a friendly one-line message and exits `0`.

Set `LOCAL_FLOW_AUDIO_RECOVERY=false` (or `audio_recovery = false` in
`local-flow.toml`) to skip the autosave entirely — no extra disk write per
utterance, at the cost of losing audio if something crashes mid-dictation.

### Microphone priority & whisper mode

`LOCAL_FLOW_MIC_PRIORITY` picks which input device `local-flow run`/`tray`
use when you have more than one microphone: a comma-separated,
priority-ordered list of case-insensitive name substrings (e.g.
`"AirPods, USB"` prefers an AirPods mic, then a USB mic, over your laptop's
built-in one). Priority is about *preference order*, not device-list order —
the first substring in the list that matches any input device wins. Leave it
empty (the default) to use the system's default input device.

```bash
uv run local-flow check   # lists input devices, marking the OS default and
                           # whichever one LOCAL_FLOW_MIC_PRIORITY selects
```

This resolution happens once, at startup: it does not re-check or fail over
mid-dictation if the chosen device disconnects while you're recording.

`LOCAL_FLOW_VAD_PRESET=whisper` helps hands-free (VAD-segmented) dictation
pick up quiet or whispered speech: it lowers the energy VAD's RMS threshold
from 500 to 150 (unless you've set `LOCAL_FLOW_VAD_ENERGY_THRESHOLD`
explicitly — note that an *explicit* value of exactly `500` is
indistinguishable from "not set" and the preset still applies) and
peak-normalizes each utterance's audio before it reaches the ASR model. The
default, `normal`, leaves both alone.

Utterances longer than `LOCAL_FLOW_MAX_UTTERANCE_MIN` minutes (default `20`)
still process and insert normally, but also emit a one-line warning — a
safety net for an accidentally stuck hands-free session or held hotkey.

### Mouse push-to-talk

Set `LOCAL_FLOW_MOUSE_BUTTON` (push-to-talk mode only) to dictate with a
mouse click instead of, or alongside, the keyboard hotkey — handy if you keep
a hand on the mouse (e.g. a side-button gaming mouse) or find a modifier key
awkward while pointing. Only non-primary buttons are supported —
`middle`/`x1`/`x2` — `left`/`right` are rejected at config load since they're
needed for normal clicking:

```bash
LOCAL_FLOW_MOUSE_BUTTON=x1 uv run local-flow run   # hold the side button to dictate
```

`LOCAL_FLOW_MOUSE_MODE` picks the gesture: `hold` (default, like the keyboard
hotkey — press and hold to record, release to insert) or `toggle` (click once
to start, click again to stop — useful for a button that's awkward to hold).

The mouse listener runs on its own thread **alongside** the keyboard hotkey
listener, not instead of it — both drive the exact same recording, so using
both at once (e.g. holding Fn *and* clicking the mouse button) is your own
foot-gun. The mouse listener has **no cancel gesture of its own**: press
`LOCAL_FLOW_CANCEL_HOTKEY` (`esc` by default) on the keyboard to discard a
mouse-started recording, same as any other push-to-talk session — this works
even though the keyboard listener's own key was never held. Pressing the
cancel key while nothing is recording does nothing (silently, no output).
If the mouse listener itself fails to start (e.g. missing Accessibility/Input
Monitoring permission), an `error:`/`hint:` pair prints to stderr instead of
silently doing nothing.

`LOCAL_FLOW_MOUSE_ENTER_BUTTON` maps a click of a second (also non-primary)
button to pressing Enter through the configured text sink — independent of
`LOCAL_FLOW_MOUSE_MODE` and always active once set, useful for e.g. hitting
"send" in a chat app without leaving the keyboard's home row untouched.
`LOCAL_FLOW_MOUSE_BUTTON` may be left empty while only
`LOCAL_FLOW_MOUSE_ENTER_BUTTON` is set — an "enter-only" configuration with no
mouse push-to-talk at all, just the Enter click. The two must be different
buttons if both are set; config load rejects them being equal.

Platform note: `x1`/`x2` (the side/back-forward buttons) are exposed by
pynput on Windows and Linux/X11, but **not** on macOS — pynput's macOS
backend only defines `left`/`middle`/`right`. On macOS, `middle` is the only
usable `LOCAL_FLOW_MOUSE_BUTTON`/`LOCAL_FLOW_MOUSE_ENTER_BUTTON` value;
setting `x1`/`x2` there raises an actionable error at listener start.

### Teach it your words

Jispr Flow can learn dictionary terms from what you actually say, two ways:

**Mine your history** — `local-flow learn` scans recent dictations for
words you use repeatedly that aren't in your dictionary yet: proper nouns,
`CamelCase`/`ALLCAPS` names, and dotted identifiers like `config.py`. It
prints a numbered list; add any of them without retyping:

```bash
uv run local-flow learn                 # 1. Kubernetes (x4) — "…deploy it on Kubernetes tomorrow…"
                                         # 2. PostgreSQL (x3) — "…back up PostgreSQL nightly…"
uv run local-flow learn --add 1 2       # add suggestions #1 and #2
uv run local-flow learn --add-all       # add everything shown
uv run local-flow learn --min-count 1 --limit 50   # see rarer/more candidates
```

Running `learn` again re-derives the same numbering as long as your history
and dictionary haven't changed in between, so a number you saw in one run is
safe to pass to `--add` in the next.

**Say it while dictating** — mid-utterance, say "add \<term\> to the
dictionary" (or "... to dictionary") and Jispr Flow strips that phrase from
the inserted text and adds `<term>` to your dictionary on the spot, e.g.
"we should containerize this, add JiSpr Flow to the dictionary, before the
demo" inserts "we should containerize this, before the demo" and adds
"JiSpr Flow". This is pure rule-based text processing, so it keeps working
even when LM Studio is unreachable. Spoken adds are extracted *after*
dictation commands, so a term that is itself a command phrase (e.g. "new
line") can't be added this way — use `local-flow learn` or edit
`dictionary.json` directly for those.

### Spoken code syntax

Say "camel case", "snake case", or "all caps" followed by 1-4 words and
Jispr Flow converts them into the literal code token, e.g.:

- "camel case order total" -> `orderTotal`
- "snake case user id" -> `user_id`
- "all caps api key" -> `API KEY`

This is pure rule-based text processing (`apply_spoken_code_syntax` in
`local_flow/polish/rules.py`), so it keeps working even when LM Studio is
unreachable, and the phrase is protected from the LLM polish pass so it
survives to be converted afterward. It is skipped entirely at
`LOCAL_FLOW_CLEANUP_LEVEL=none` (verbatim mode: nothing is transformed).

The conversion window (up to four words after the trigger) stops at the
first common connector/filler word — "and", "then", "so", "but", "or",
"with", "to", "for", "the", "a", "an", "is", "are", "was", "please" — so
continuous dictation like "snake case user id and then send it" only
converts "user id", leaving "and then send it" as ordinary trailing text
instead of getting folded into the token. If the trigger is followed
immediately by nothing but connector words (e.g. "snake case and"),
there's nothing left to convert and the whole phrase is left exactly as
spoken.

**Known false-positive risk:** this is still a simple deterministic rule,
not a language model. Connector bounding fixes the common case of a
trigger phrase running on into unrelated trailing speech, but a sentence
whose words right after the trigger aren't connectors will still convert
(e.g. "I like snake case naming better" -> "I like naming_better"). Speak
the trigger phrase right next to the words you want converted, with
nothing else following, to avoid this. And because the window stops at
connector words, an identifier you actually want that starts with one of
them (e.g. the literal token `to_do`) can't be produced by this feature at
all — add it to the dictionary or a snippet instead.

### Transcribe audio files

`local-flow transcribe` runs an existing audio file through the same local
ASR (and, optionally, polish) pipeline as live dictation -- a feature Wispr
Flow itself doesn't offer, since it only ever transcribes live microphone
input:

```bash
uv run local-flow transcribe voice-memo.m4a --polish
# == meeting-notes.wav ==
# discussed Q3 roadmap and agreed to ship the export feature first
```

- Accepts containers supported by the selected real backend -- WAV, MP3,
  M4A, FLAC, and more -- at any sample rate; no manual conversion needed.
  (The `mock` ASR backend used in tests/CI only reads plain WAV.)
- Multiple files may be given at once; each one's output is preceded by a
  `== filename ==` header once there's more than one file. Text goes to
  stdout; a `transcribing <name>...` progress line goes to stderr per file.
- `--polish` runs each file's raw transcript through the exact same
  rules-plus-LLM cleanup as `local-flow polish` (dictionary, snippets, and
  dictation commands included); it degrades to rules-only if LM Studio is
  unreachable, same as everywhere else.
- `--copy` puts the *last* file's final text on the clipboard.
- `--language XX` overrides `LOCAL_FLOW_ASR_LANGUAGE` for this one run only
  (still validated against `.en`-suffixed models, same as the configured
  default).
- Nothing is inserted into any app and nothing is written to history --
  this is a pure text-out command.

### Benchmark ASR backends

`local-flow benchmark-asr` loads one backend/model, performs optional warmup
runs, then repeatedly transcribes each local file. It prints model-load time,
per-run and median/p95 latency, real-time factor (RTF), transcript, and WER
when matching references are supplied. `--json` writes the same result with
full per-run precision for comparisons or regression tracking.

```bash
uv run local-flow benchmark-asr short.wav jargon.wav \
  --reference "expected short transcript" \
  --reference "expected jargon transcript" \
  --runs 3 --warmup 1 --json /tmp/asr-benchmark.json

# Override the configured backend/model for a one-off comparison:
uv run local-flow benchmark-asr sample.wav \
  --backend mlx-whisper \
  --model mlx-community/whisper-small.en-mlx \
  --reference "expected transcript"
```

References are positional: repeat `--reference` once per audio file in the
same order. Audio and generated JSON stay wherever you place them; the
benchmark does not write history or copy source audio into the data dir.

### Text transforms

`local-flow transform <name>` applies a named, reusable AI rewrite to text --
either passed directly (headless, scriptable) or captured from whatever is
currently highlighted in the frontmost app:

```bash
uv run local-flow transform --list                       # show available names
uv run local-flow transform Polish --text "hey can u fix the bug pls"
uv run local-flow transform "Prompt Engineer" --text "make the tests faster"
uv run local-flow transform Polish --selection            # transform the current selection
```

Transforms are name -> prompt pairs stored in `<data dir>/transforms.json`,
hand-editable like `styles.json`/`snippets.json`. Two ship built in:
**Polish** (clarity/concision rewrite) and **Prompt Engineer** (restructures
text into a goal/context/constraints/output-format AI prompt). They're
seeded into `transforms.json` only the first time the store is created --
add, remove, or edit entries freely afterward and Jispr Flow leaves your file
alone (unlike `styles.json`, built-ins added in a later version are *not*
backfilled into an existing `transforms.json`).

`--selection` captures the current OS selection via a clipboard round-trip
(save the clipboard, clear it, synthesize Cmd+C/Ctrl+C, poll briefly for a
change) so it works in any app without accessibility-API integration; if
nothing is highlighted it fails with a hint instead of transforming an empty
string. On success the selection is replaced in place (write the result to
the clipboard, synthesize Cmd+V/Ctrl+V, briefly wait for the paste to land,
then restore your original clipboard content) and a confirmation prints to
stderr. `--text` skips the clipboard entirely and prints the result to
stdout -- useful for scripting or when nothing is selected.

### Transform anywhere

`LOCAL_FLOW_TRANSFORM_HOTKEY` (push-to-talk mode only, like mouse push-to-talk)
turns the `local-flow transform ... --selection` flow above into a global
hotkey: highlight text in any app, tap the key, and it's rewritten in place --
no CLI needed while `local-flow run` is active. Empty (the default) disables
the feature entirely.

```bash
LOCAL_FLOW_TRANSFORM_HOTKEY=f6 uv run local-flow run   # tap F6 to transform the selection
```

`LOCAL_FLOW_TRANSFORM_DEFAULT` (default `Polish`) picks which
`transforms.json` entry the hotkey applies; it's resolved once at startup --
an unknown name prints a warning and disables just the transform hotkey (the
rest of `local-flow run` keeps working) rather than crashing or failing every
tap. Nothing selected also just warns ("no text selected"); your clipboard is
always restored to what it held before the tap, on success or failure alike.
Unlike push-to-talk, this is a single tap (key-down), not hold-and-release --
same plain-key-only limitation as the main/cancel hotkeys (no chords).

### Voice command mode

`LOCAL_FLOW_COMMAND_HOTKEY` (push-to-talk mode only) adds a second
push-to-talk key for *spoken* edit instructions -- Wispr Flow calls this
"command mode": hold the key, say what you want done ("make this more
formal", "turn it into bullets"), release. Empty (the default) disables it
entirely; it runs independently of (and alongside) the main dictation
hotkey, with its own recording state. Both ultimately record from the same
microphone/`SounddeviceSource`, though, and opening it twice at once is
device contention, not two clean recordings -- so holding both hotkeys down
at the same time gets the *second* one refused outright (a "microphone busy;
finish the other recording first" warning, no recorder started) rather than
corrupting either recording.

```bash
LOCAL_FLOW_COMMAND_HOTKEY=f7 uv run local-flow run
# hold F7, say "make this more formal", release -> the current
# selection (or, if nothing is selected, your last dictation) is rewritten
```

The current OS selection is captured the moment you release the key (before
transcription starts, so it reflects whatever was highlighted while you were
speaking) via the same clipboard round-trip as the transform hotkey/
`local-flow transform --selection`. When something was selected, the result
*replaces* it in place and your clipboard is restored afterward. When nothing
is selected, it falls back to the same target `local-flow command` uses --
your last dictation -- and inserts the result through the configured sink
instead. Dictionary term casing is enforced either way. Any failure (nothing
heard, LM Studio down, no target text at all) reports a warning instead of
crashing the loop. Like the transform hotkey, this has no cancel gesture of
its own and is a plain key (no chords).

### Auto-transform

`LOCAL_FLOW_AUTO_TRANSFORM` names a `transforms.json` entry to run
automatically on *every* dictation, right after dictionary/snippet/dictation-
command handling and just before the text is inserted -- e.g. set it to
`Polish` to always get an extra clarity pass beyond the normal cleanup level.
Empty (the default) is a complete no-op. An unknown name fails fast with a
`ConfigError` (listing known transform names) as soon as `local-flow run`
starts, rather than failing silently on every dictation. It's skipped
whenever there's no chat client configured or `LOCAL_FLOW_CLEANUP_LEVEL=none`
(a full verbatim bypass); an LM Studio failure during the auto-transform call
degrades gracefully -- the un-transformed text still gets inserted, with a
warning explaining why.

This is intentionally more severe than an unknown `LOCAL_FLOW_TRANSFORM_DEFAULT`
(see "Transform anywhere" above), which only warns and disables that one
hotkey: `transform_default` gates an opt-in, per-tap feature, so failing soft
just means one fewer hotkey; `auto_transform` gates something that would
otherwise silently no-op (or warn) on *every single dictation*, so a typo is
worth stopping the whole process for, immediately, rather than discovering it
utterance by utterance.

```bash
LOCAL_FLOW_AUTO_TRANSFORM=Polish uv run local-flow run   # every dictation gets an extra polish pass
```

### Context-aware dictation

Jispr Flow can peek at the focused field's *existing* text (best-effort,
read-only) so the polish pass continues what's already there instead of
re-greeting or clashing with it: dictating "thanks for the referral" after a
note that ends "Dear Dr. Adithya," comes back as a continuation, not a
second greeting, and any name spelled in the field (e.g. "Adithya") is
reused verbatim rather than re-guessed from audio.

This is on by default (`LOCAL_FLOW_CONTEXT_AWARENESS=true`); set it to
`false` to disable entirely. It only affects the LLM polish prompt (skipped
completely at `LOCAL_FLOW_CLEANUP_LEVEL=none`, same as everything else
LLM-related) and never changes what gets typed/pasted beyond that.

**Privacy:** the little bit of field text this reads (the text immediately
before your cursor, tail-capped at 1000 characters, plus any current
selection) is sent only to your local LM Studio server as part of the
existing polish prompt -- exactly like your dictation itself. It is never
written to disk, never logged, and never sent anywhere else. Secure fields
(password inputs) are protected by macOS itself: the Accessibility API
withholds a secure field's contents from any reader, this app included, so
there's nothing for `MacAXFieldText` to read there in the first place --
Jispr Flow adds no extra guard of its own beyond that OS-level behavior, and
whatever (non-secure) field text it does read is, as above, sent only to
your local LM Studio server and never stored.

**Platform support:** macOS only for now, via the Accessibility API (needs
the same *Accessibility* permission already required for paste/type -- see
"Platform permission notes" below); if the read fails for any reason
(permission denied, an app that doesn't expose it, etc.) it silently
degrades to no context, same as if the feature were off. Windows and Linux
currently always report no context: Windows would need COM interop
(`comtypes`), a dependency this project doesn't have yet, so Jispr Flow
ships an honest stub there rather than untested COM-automation code; Linux
has no accessibility-text adapter yet. Dictation itself is unaffected on
either platform -- this is strictly an extra hint for the polish pass when
available.

## Cleanup levels

`LOCAL_FLOW_CLEANUP_LEVEL` controls how aggressively the polish pass rewrites
your rough transcript, from `none` (verbatim) to `high` (rewritten for
concision). Default: `medium` (today's behavior, unchanged).

| Level | Rule cleanup | LM Studio call | Behavior |
|---|---|---|---|
| `none` | no | no | Insert exactly what you said, fillers and all. |
| `light` | yes | yes | Fix grammar and remove fillers only; no rephrasing. |
| `medium` | yes | yes | Punctuation, capitalization, grammar, artifacts (default). |
| `high` | yes | yes | Rewrite for concision and polish, preserving meaning. |

`light`/`medium`/`high` all instruct the model to turn spoken enumerations
("first ..., second ..., third ...") into a proper numbered or bulleted
list, and all three fall back to rule-based cleanup only (no rewrite) if LM
Studio is unreachable, exactly like today's `medium` behavior.

**`none` is special: it is not just "gentle cleanup," it is a full bypass.**
No filler/backtracking rules run and LM Studio is never contacted -- the
inserted text is your dictated words exactly as ASR produced them. This is
*not* the same as disabling personalization, though: dictionary term
correction, snippet expansion, dictation commands ("new line", "press
enter"), and spoken "add \<term\> to the dictionary" all still run, because
those are personalization features applied by
`local_flow.pipeline.DictationPipeline` after the polish step, not cleanup
performed by the polish step itself. So at `none`, "the jispr flow rollout"
still becomes "the JiSpr Flow rollout" if `JiSpr Flow` is in your
dictionary, and "press enter" still triggers the Enter key -- only the
filler-removal/grammar/rewrite pass is skipped.

## Streaming

`LOCAL_FLOW_STREAMING=sentence` (hands-free mode only) lowers dictation
latency by shortening the pause that closes an utterance: instead of waiting
for `LOCAL_FLOW_VAD_SILENCE_MS` (default 600ms) of silence, it closes and
inserts each chunk after just `LOCAL_FLOW_STREAMING_PAUSE_MS` (default
300ms). In practice, each sentence is transcribed, polished, and inserted
while you're still speaking the next one, instead of everything landing at
once when you finally stop talking.

```bash
LOCAL_FLOW_STREAMING=sentence LOCAL_FLOW_MODE=hands-free uv run local-flow run
```

Trade-offs to know before turning this on:

- **Latency vs. accuracy.** A shorter pause threshold means shorter, more
  frequent chunks — lower time-to-insertion, but the ASR/polish model sees
  less context per chunk, so mid-sentence pauses (a breath, a filler word)
  can split a sentence into two insertions more readily than the default
  threshold would.
- **"Scratch that" only reaches within the current chunk.** Backtracking
  commands (e.g. "scratch that") operate on the rough transcript of the
  chunk being processed; once a chunk has already been inserted, an earlier
  chunk is not reachable for correction. History also records one entry per
  chunk rather than one per full utterance.
- **Push-to-talk is unaffected.** Streaming is a hands-free-only feature;
  with `LOCAL_FLOW_MODE=push-to-talk`, a non-`off` `LOCAL_FLOW_STREAMING`
  prints a one-line notice (`streaming requires hands-free mode; ignoring`)
  and dictation behaves exactly as if streaming were off.

`LOCAL_FLOW_STREAMING=live-preview` (hands-free mode only) shows a rough,
continuously-updating partial transcript on the console (or the tray
tooltip) *while you're still speaking*, so you get feedback that dictation
is working before you pause. Under the hood, mic frames are teed into a
second, windowed re-transcription pass (re-transcribing the accumulated
utterance roughly once a second) purely for display; nothing about the
actual insertion path changes:

```bash
LOCAL_FLOW_STREAMING=live-preview LOCAL_FLOW_MODE=hands-free uv run local-flow run
```

- **The final inserted text is unaffected.** The preview is display-only —
  it never feeds into the dictionary/snippet/command-mode/history pipeline.
  The utterance's real transcription, polish, and insertion happen exactly
  as they do with streaming off, from the same buffered audio, once you
  pause. If the rough preview and the final insert ever disagree (e.g. the
  preview caught a word the final pass corrected), the final insert wins.
- **It does not lower time-to-insertion.** Unlike `sentence` mode,
  `live-preview` doesn't change when text lands in your editor — only
  `sentence` mode does that. `live-preview` only changes what you *see*
  while speaking.
- **Eyeballing the difference.** To compare against `off`, dictate the same
  sentence once with `LOCAL_FLOW_STREAMING=off` and once with
  `LOCAL_FLOW_STREAMING=live-preview`: with `off` the terminal/tray stays
  silent until you pause and the final text appears all at once; with
  `live-preview` a rough line (prefixed with `…`) updates on the console (or
  the tray tooltip) within about a second of starting to speak, well before
  the pause-triggered final insert — that gap is the perceived-latency
  win, even though wall-clock time to the *final* insert is the same either
  way. `sentence` mode is what to reach for if you want the final text
  itself to land earlier.
- **Push-to-talk is unaffected**, same as `sentence` mode above: a
  non-`off` `LOCAL_FLOW_STREAMING` with `LOCAL_FLOW_MODE=push-to-talk`
  prints the same one-line notice and behaves exactly as if streaming were
  off.

## Tray app

`local-flow tray` runs the same dictation loop as `local-flow run`, but as a
menu-bar/tray icon (macOS/Windows/most Linux desktops) with live state and
quick style/language switching, instead of a terminal window. It needs the
optional `tray` extra:

```bash
uv sync --extra tray
uv run local-flow tray
```

What the icon looks like at each state (see `local_flow/tray/icons.py` /
`local_flow/tray/state.py`):

| State | Icon color | Tooltip |
|---|---|---|
| idle | gray | `local-flow — idle` |
| recording | red | `local-flow — recording` |
| processing | amber | `local-flow — processing` |
| inserted (flashes back to idle) | gray | `inserted: <first 40 chars>` |
| error / warning | dark red with "!" | `local-flow — error: <detail>` (also raises a desktop notification) |

Menu:

- **Dictation: Start/Stop** — in `--mode hands-free`, actually starts/stops
  the capture loop (a `threading.Event` the loop checks per audio frame, so
  Stop takes effect within a frame -- effectively instant, not mid-utterance
  or between utterances); in push-to-talk mode this is a disabled status
  label ("listening for hotkey") since the hotkey itself already
  starts/stops each utterance.
- **Mode** — shows the configured capture mode (informational).
- **Style** — one item per name in `styles.json` (built-ins: `default`,
  `professional`, `casual`, `email`, `chat`, plus any you've added);
  clicking sets the style used for the *next* dictation. Hidden if there
  are no styles (shouldn't happen — `styles.json` always ships defaults).
- **Language** — one item per code in `LOCAL_FLOW_LANGUAGES` (e.g.
  `en,de,fr`); clicking sets the ASR language for the *next* utterance.
  Hidden entirely when `LOCAL_FLOW_LANGUAGES` is unset/empty. Needs a
  multilingual ASR model (`LOCAL_FLOW_ASR_MODEL=small`, not `small.en`).
- **Open data folder** — opens the data dir (`dictionary.json`,
  `snippets.json`, `styles.json`) in your file manager.
- **Quit** — stops the dictation loop and the tray icon.

## Architecture

```
            ┌───────────── audio adapters ─────────────┐
 microphone ─► AudioSource (sounddevice | mock) ─► VAD (energy | webrtc | mock)
                                                        │  speech segments
                                                        ▼
                          ASR Transcriber (faster-whisper | MLX Whisper | mock)
                                                        │  rough transcript
                                                        ▼
                    rule cleanup (fillers, backtracking)   [pure Python]
                                                        │  cleaned text
                                                        ▼
                LM Studio polish (OpenAI-compatible API, localhost only)
                                                        │  polished text
                                                        ▼
        dictionary enforcement ─► snippet expansion ─► dictation commands
                                                        │  final text + key actions
                                                        ▼
            TextSink (clipboard+paste → typing → clipboard-only | fake)
```

Command mode reuses the same LM Studio client and TextSink: instruction +
target text (explicit or last transcript) → transformed text → insertion.
Every arrow above is an adapter interface with a mock, so the entire pipeline
runs headlessly in CI. See [docs/architecture.md](docs/architecture.md).

## Platform permission notes & hotkey limitations

- **macOS** — the terminal running Jispr Flow needs *Microphone*,
  *Accessibility* (to paste/type), and *Input Monitoring* (global hotkey)
  permissions under System Settings → Privacy & Security. macOS prompts on
  first use; restart the terminal after granting.
- **Windows** — allow microphone access for desktop apps (Settings →
  Privacy & security → Microphone). Global hotkeys and synthetic paste
  generally work; elevated (admin) windows won't accept keystrokes from a
  non-elevated local-flow.
- **Linux (X11)** — install `xclip` or `xsel` for the clipboard; hotkeys and
  paste work via pynput.
- **Linux (Wayland)** — compositors block global key capture and synthetic
  keystrokes for security. Use `--mode hands-free` (no hotkey needed) with
  the clipboard insert method (`LOCAL_FLOW_INSERT_METHOD=clipboard`) and
  paste manually, or install `wl-clipboard`.
- Push-to-talk keys: **Fn** (macOS only — other OSes never see the Fn key;
  needs Accessibility and Input Monitoring permission; while local-flow is
  running it consumes Fn press/release events so macOS Dictation or another
  Fn-based listener cannot insert a duplicate), **Space** (hold to dictate,
  quick tap still types a space; macOS/Windows — Linux/X11 cannot suppress
  the key, use another key or hands-free mode there), or any single pynput key name
  (`f9`, `f8`, `scroll_lock`, …). Chord hotkeys are not supported yet.
  Press `esc` (configurable) to throw away a dictation mid-recording (with
  the `fn` hotkey only `esc` is supported as the cancel key).
  Note: using Fn as a modifier (e.g. Fn+arrow) also triggers dictation and
  the Fn event is not forwarded to other apps — pick another key if you use
  Fn combos heavily.
  When paste fails, Jispr Flow falls back to synthetic typing, then to
  clipboard-only with a message — the text is never lost.
- Mouse push-to-talk (`LOCAL_FLOW_MOUSE_BUTTON`, see "Mouse push-to-talk"):
  `middle` works everywhere pynput's mouse backend runs; `x1`/`x2` (side
  buttons) work on Windows and Linux/X11 but not macOS. It has no cancel
  gesture — use the keyboard cancel key. Runs alongside the keyboard hotkey,
  never instead of it.
- Transform hotkey (`LOCAL_FLOW_TRANSFORM_HOTKEY`, see "Transform anywhere"),
  voice command hotkey (`LOCAL_FLOW_COMMAND_HOTKEY`, see "Voice command
  mode"), and the scratchpad hotkey (`LOCAL_FLOW_SCRATCHPAD_HOTKEY`, see
  "Dictate to scratchpad"): same plain-single-pynput-key-only limitation as
  the main hotkey — no chords. None has a cancel gesture of its own.
  `local-flow run` refuses to start if any two of hotkey/transform_hotkey/
  command_hotkey/scratchpad_hotkey are set to the same key (a config error
  with a hint, not a runtime surprise).
- `local-flow pad --window` needs a Tk-enabled Python (see "Scratchpad" →
  "Floating window"); it runs as its own blocking main program, not inside
  `local-flow run`, so tkinter's single-main-thread requirement is never in
  tension with the hotkey/audio main thread.
- Context-aware dictation (`LOCAL_FLOW_CONTEXT_AWARENESS`, see
  "Context-aware dictation") reuses the same macOS *Accessibility*
  permission as paste/type — no separate grant needed. It is macOS-only for
  now; on Windows/Linux it always reports no context (a documented gap, not
  a bug) and dictation is otherwise unaffected.

## Manual test checklist

Automated tests cover the pipeline with mocks; these need a human, a mic,
and a running LM Studio:

1. `uv run local-flow check` → LM Studio reachable, model listed, extras installed.
2. `uv run local-flow run`, hold your push-to-talk key (Fn on macOS by
   default), say "hello world um this is a test", release → polished text
   appears in the focused editor.
3. Say "send it to Bob, scratch that, send it to Alice" → only Alice remains.
4. Add a dictionary term, dictate it lowercase → canonical casing inserted.
5. Add a snippet ("sig block"), dictate its trigger → expansion inserted.
6. End a dictation with "press enter" → Enter is pressed after insertion.
7. `--mode hands-free`: speak, pause ~0.6 s → text inserts without a hotkey.
8. Stop the LM Studio server, dictate → rule-cleaned text still inserts and a
   warning explains that polish was skipped.
9. Focus an app that blocks paste → typing fallback (or clipboard message).
10. `uv run local-flow command "make this a bullet list" --text "..."`.
11. `LOCAL_FLOW_HOTKEY=fn uv run local-flow run` (macOS): hold Fn → dictate →
    release inserts polished text.
12. `LOCAL_FLOW_HOTKEY=space uv run local-flow run`: tap Space in an editor →
    a normal space appears; hold Space → dictation starts.
13. Press Esc mid-dictation → nothing is inserted and "dictation discarded"
    is printed.
14. Start `uv run local-flow run`, dictate, then kill the process (`kill -9`)
    before it finishes inserting → a WAV appears under
    `<data dir>/pending/`; `uv run local-flow recover` transcribes/polishes/
    inserts it and the file is gone afterward.

Tray app (`uv sync --all-extras && uv run local-flow tray`):

15. The icon appears in the menu bar; it turns red while holding the hotkey
    (or during a hands-free utterance), amber while processing, and back to
    gray afterward; it raises a desktop notification on errors/warnings.
16. Tray **Style** submenu → switch to `email` → the next dictation is
    structured as an email (greeting, short paragraphs, sign-off).
17. Tray **Language** submenu (with `LOCAL_FLOW_LANGUAGES=en,de` and a
    multilingual model, e.g. `LOCAL_FLOW_ASR_MODEL=small`) → switch to `de`,
    dictate in German → transcribed/polished in German.
18. `--mode hands-free`: **Dictation: Start/Stop** actually starts and stops
    capture; in push-to-talk mode the same menu item is a disabled
    "listening for hotkey" label.

Setup wizard (`uv run local-flow setup` on a machine without a config yet):

19. The dependency/LM Studio probe report prints, the questions accept
    Enter-for-default and reject/re-ask on an invalid answer, and the
    resulting `~/.config/local-flow/config.toml` (or wherever you pointed it)
    works with `local-flow check`/`local-flow run` without edits.
20. Re-running `setup` against an existing config asks to overwrite; answering
    anything but `y` leaves the existing file untouched.

Microphone priority & whisper mode:

21. `LOCAL_FLOW_MIC_PRIORITY="AirPods"` with AirPods connected → `local-flow
    check`'s input-device listing marks the AirPods entry as selected.
22. `LOCAL_FLOW_VAD_PRESET=whisper` with `--mode hands-free` → speaking at a
    whisper still transcribes (compare against `vad_preset=normal`, where the
    same whisper often goes undetected).

Mouse push-to-talk:

23. `LOCAL_FLOW_MOUSE_BUTTON=middle uv run local-flow run` → hold the middle
    mouse button to dictate, release to insert (also works with a side-button
    mouse via `x1`/`x2` on Windows/Linux). With `LOCAL_FLOW_MOUSE_MODE=toggle`,
    one click starts recording and a second click stops/inserts it. `esc`
    (keyboard) still discards a mouse-started recording, even though you
    never held any keyboard key — nothing is inserted and "dictation
    discarded" prints. Pressing `esc` again while idle (nothing recording)
    does nothing.
24. `LOCAL_FLOW_MOUSE_ENTER_BUTTON=middle uv run local-flow run` (leave
    `LOCAL_FLOW_MOUSE_BUTTON` unset) → clicking the middle button presses
    Enter through the sink; no mouse push-to-talk is offered (only the
    keyboard hotkey dictates).

Transcribe & text transforms:

25. `uv run local-flow transcribe memo.m4a --polish` → polished notes print
    to stdout; with two files, each is preceded by a `== filename ==` header.
26. Highlight text in any app, run `uv run local-flow transform Polish
    --selection` → the selection is replaced with the rewritten text and your
    original clipboard content is restored afterward.
27. `LOCAL_FLOW_TRANSFORM_HOTKEY=f6 uv run local-flow run` → select text in
    any app, tap F6 → it's rewritten in place with `transform_default`
    (`Polish` by default) and your clipboard is restored afterward. Tap it
    with nothing selected → a "no text selected" warning prints; nothing is
    changed.
28. `LOCAL_FLOW_COMMAND_HOTKEY=f7 uv run local-flow run` → select text, hold
    F7, say "make this more formal", release → the selection is replaced
    with the spoken edit. Release F7 with nothing selected → your last
    dictation is transformed and inserted via the normal sink instead.
29. `LOCAL_FLOW_AUTO_TRANSFORM=Polish uv run local-flow run` → every
    dictation lands pre-polished by the named transform, on top of the
    normal cleanup level.

Personal insights & scratchpad:

30. `uv run local-flow stats` → totals, streak, and heatmap print for your
    real history; `--since 7d`/`--since all` narrow/widen the window, and an
    empty store (or empty window) prints a friendly message instead of
    zeros.
31. `local-flow pad --append "first thought"` then `--show` → note content
    printed; `local-flow pad --window` shows it, stays on top of other
    windows, and picks up a second terminal's `pad --append` within ~500ms.
    Typing in the window autosaves (check the `.md` file on disk) about a
    second after you stop typing.
32. `LOCAL_FLOW_SCRATCHPAD_HOTKEY=f8 uv run local-flow run` → tap F8, dictate
    → text lands in the active scratchpad note, not the focused app; tap F8
    again → normal insertion resumes. Works the same whether or not
    `pad --window` is open in another terminal.

Context-aware dictation:

33. (macOS) Focus a text field/document ending "Dear Dr. Adithya," and
    dictate "thanks for the referral" → the polished result continues
    naturally (no repeated greeting) and "Adithya" keeps its exact spelling.
    `LOCAL_FLOW_CONTEXT_AWARENESS=false uv run local-flow run` → the same
    dictation instead polishes with no awareness of the existing text.

## Development

```bash
uv run pytest          # all tests are headless (mocked ASR/VAD/LLM/sinks)
uv run ruff check .    # lint
```

Licensed under the MIT license. Not affiliated with, endorsed by, or derived
from Wispr Flow or any other proprietary dictation product.
