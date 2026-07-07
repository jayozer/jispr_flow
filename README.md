# local-flow

Local-first, open-source desktop dictation in the spirit of "flow"-style
dictation apps — built from scratch, with **no** proprietary code, branding,
assets, or network services. Speak; a local ASR model transcribes; a local
LM Studio model polishes; the result lands in your active app.

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
4. Optionally set `LOCAL_FLOW_LMSTUDIO_MODEL`; left empty, local-flow
   auto-picks the first loaded model.

LM Studio is used **only** for text polish and command mode — never for
speech recognition. ASR is a separate local adapter.

## Recommended ASR models (faster-whisper)

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
| ASR model | `LOCAL_FLOW_ASR_MODEL` | `small.en` |
| ASR language | `LOCAL_FLOW_ASR_LANGUAGE` | `en` (or an ISO code, or `auto`) |
| Tray languages | `LOCAL_FLOW_LANGUAGES` | *(empty; comma-separated codes)* |
| VAD backend | `LOCAL_FLOW_VAD_BACKEND` | `energy` (or `webrtc`) |
| Mode | `LOCAL_FLOW_MODE` | `push-to-talk` (or `hands-free`) |
| Hotkey | `LOCAL_FLOW_HOTKEY` | fn (macOS) / f9 |
| Hotkey hold threshold | `LOCAL_FLOW_HOTKEY_SPACE_HOLD_MS` | `250` |
| Cancel hotkey | `LOCAL_FLOW_CANCEL_HOTKEY` | `esc` |
| Style | `LOCAL_FLOW_STYLE` | `default` |
| Cleanup level | `LOCAL_FLOW_CLEANUP_LEVEL` | `medium` (or `none`/`light`/`high`; see "Cleanup levels") |
| Data dir | `LOCAL_FLOW_DATA_DIR` | `~/.local/share/local-flow` |
| Streaming | `LOCAL_FLOW_STREAMING` | `off` (or `sentence`/`live-preview`, hands-free only; see "Streaming") |
| Streaming pause | `LOCAL_FLOW_STREAMING_PAUSE_MS` | `300` |

Personalization lives in the data dir as hand-editable JSON:
`dictionary.json` (canonical terms), `snippets.json` (trigger → expansion),
`styles.json` (named style rules + the active one).

## Per-app styles & insertion

local-flow can look at the frontmost app/window when a dictation finishes and
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
entirely (e.g. if you don't want local-flow querying the active window).

## Use

```bash
uv run local-flow setup      # interactive onboarding wizard; writes config.toml
uv run local-flow check      # diagnose LM Studio / ASR / audio / clipboard
uv run local-flow run        # live dictation (hold Fn/Space, speak, release)
uv run local-flow run --mode hands-free   # VAD-segmented, no hotkey needed
uv run local-flow polish "um send the uh draft, scratch that, the final doc"
uv run local-flow command "make this formal" --text "hey can u fix the bug"
uv run local-flow demo       # headless end-to-end proof with mocks
uv run local-flow history                 # list recent dictations, newest first
uv run local-flow history --search invoice --limit 5
uv run local-flow history --verbose       # also show the rough (pre-polish) transcript
uv run local-flow history --clear         # delete the local history file
uv run local-flow history --show 1        # print record #1's full rough + final text
uv run local-flow history --reinsert-raw 1   # undo a bad AI edit: re-insert record #1's rough text
uv run local-flow learn                   # mine history for candidate dictionary terms
uv run local-flow learn --add 1 2         # add suggestions #1 and #2 to the dictionary
uv run local-flow tray                    # menu-bar app (see "Tray app" below)
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
- Out-of-range `N` fails with a friendly error naming how many records
  exist, instead of a traceback.

### Teach it your words

local-flow can learn dictionary terms from what you actually say, two ways:

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
dictionary" (or "... to dictionary") and local-flow strips that phrase from
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
local-flow converts them into the literal code token, e.g.:

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
                                       ASR Transcriber (faster-whisper | mock)
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

- **macOS** — the terminal running local-flow needs *Microphone*,
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
  needs Input Monitoring permission), **Space** (hold to dictate, quick tap
  still types a space; macOS/Windows — Linux/X11 cannot suppress the key, use
  another key or hands-free mode there), or any single pynput key name
  (`f9`, `f8`, `scroll_lock`, …). Chord hotkeys are not supported yet.
  Press `esc` (configurable) to throw away a dictation mid-recording (with
  the `fn` hotkey only `esc` is supported as the cancel key).
  Note: using Fn as a modifier (e.g. Fn+arrow) also triggers dictation
  start/stop — pick another key if you use Fn combos heavily.
  When paste fails, local-flow falls back to synthetic typing, then to
  clipboard-only with a message — the text is never lost.

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

Tray app (`uv sync --all-extras && uv run local-flow tray`):

14. The icon appears in the menu bar; it turns red while holding the hotkey
    (or during a hands-free utterance), amber while processing, and back to
    gray afterward; it raises a desktop notification on errors/warnings.
15. Tray **Style** submenu → switch to `email` → the next dictation is
    structured as an email (greeting, short paragraphs, sign-off).
16. Tray **Language** submenu (with `LOCAL_FLOW_LANGUAGES=en,de` and a
    multilingual model, e.g. `LOCAL_FLOW_ASR_MODEL=small`) → switch to `de`,
    dictate in German → transcribed/polished in German.
17. `--mode hands-free`: **Dictation: Start/Stop** actually starts and stops
    capture; in push-to-talk mode the same menu item is a disabled
    "listening for hotkey" label.

Setup wizard (`uv run local-flow setup` on a machine without a config yet):

18. The dependency/LM Studio probe report prints, the questions accept
    Enter-for-default and reject/re-ask on an invalid answer, and the
    resulting `~/.config/local-flow/config.toml` (or wherever you pointed it)
    works with `local-flow check`/`local-flow run` without edits.
19. Re-running `setup` against an existing config asks to overwrite; answering
    anything but `y` leaves the existing file untouched.

## Development

```bash
uv run pytest          # all tests are headless (mocked ASR/VAD/LLM/sinks)
uv run ruff check .    # lint
```

Licensed under the MIT license. Not affiliated with, endorsed by, or derived
from Wispr Flow or any other proprietary dictation product.
