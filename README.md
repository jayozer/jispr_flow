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
uv sync                      # core + dev deps; runs headless demo and tests
uv run local-flow demo       # prove the whole pipeline with mocks, no permissions

# For real use, add the extras you need:
uv sync --extra asr          # faster-whisper (local speech-to-text)
uv sync --extra audio        # sounddevice (mic) + webrtcvad
uv sync --extra desktop      # pynput (hotkeys/paste) + pyperclip (clipboard)
# or everything:
uv sync --all-extras
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
| VAD backend | `LOCAL_FLOW_VAD_BACKEND` | `energy` (or `webrtc`) |
| Mode | `LOCAL_FLOW_MODE` | `push-to-talk` (or `hands-free`) |
| Hotkey | `LOCAL_FLOW_HOTKEY` | fn (macOS) / f9 |
| Hotkey hold threshold | `LOCAL_FLOW_HOTKEY_SPACE_HOLD_MS` | `250` |
| Cancel hotkey | `LOCAL_FLOW_CANCEL_HOTKEY` | `esc` |
| Style | `LOCAL_FLOW_STYLE` | `default` |
| Data dir | `LOCAL_FLOW_DATA_DIR` | `~/.local/share/local-flow` |

Personalization lives in the data dir as hand-editable JSON:
`dictionary.json` (canonical terms), `snippets.json` (trigger → expansion),
`styles.json` (named style rules + the active one).

## Use

```bash
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

## Development

```bash
uv run pytest          # all tests are headless (mocked ASR/VAD/LLM/sinks)
uv run ruff check .    # lint
```

Licensed under the MIT license. Not affiliated with, endorsed by, or derived
from Wispr Flow or any other proprietary dictation product.
