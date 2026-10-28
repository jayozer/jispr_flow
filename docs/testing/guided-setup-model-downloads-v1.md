# Guided setup and model downloads v1

Branch: `codex/guided-setup-model-downloads`, stacked on the accepted
`codex/teach-me-once` checkpoint `7bfc184`. The Living Caret and Teach Me Once
branches remain available independently. Scope is native setup and local speech
model downloads; existing dictation, personalization, and insertion remain the
base behavior.

## Native experience

- First launch opens **Welcome to JiSpr** without starting speech recognition.
  Choose a language, then a compatible speech model. Existing saved settings
  remain unchanged while browsing or downloading.
- Five curated choices cover Parakeet v3, multilingual and English MLX Whisper
  Small, MLX Whisper Turbo, and CPU-compatible Faster Whisper Small. Cards show
  download size, installed/verified status, and hardware or missing-backend
  requirements. Parakeet requires FFmpeg.
- **Download** fetches model files explicitly. **Verify model** checks an existing
  compatible local cache before reuse. Progress counts bytes transferred over
  the network; local verification is indeterminate and a fully cached operation
  can complete with zero transferred bytes. Cancel and Retry are available.
- **Apply and continue** saves the verified local model, language, and polish
  choice together, selecting the custom ASR profile. Other preferences are kept.
  An active dictation, a failed engine stop, or an environment-owned setting
  produces an error instead of applying part of the selection.
- New configurations default to local rules for writing cleanup, so setup does
  not depend on an LM Studio server. An existing configured polish choice is
  retained until the user changes it. LM Studio remains an optional local choice.
- The permissions page separately checks **Accessibility**, **Input Monitoring**,
  and **Microphone**, with normal macOS requests and links to their settings.
- Practice explicitly starts the selected engine and focuses a text editor.
  Instructions follow the configured hotkey or hands-free mode. The microphone
  meter and recording state provide feedback. Finish requires a recording,
  a new dictation event for JiSpr, and matching words arriving in that editor;
  typing alone, a start reply, or clipboard-only fallback does not qualify.
- **Later** or closing the window cancels an active model operation and defers
  setup without marking it successful. Deferred setup does not automatically
  start the engine on a later launch. The menu still offers explicit Start
  Dictation. Successful completion enables normal engine startup next time.
- Reopen **JiSpr → Guided Setup & Models…** at any time. **Settings → Models →
  Manage Models…** opens the model stage directly.

Completion/defer preferences belong to native UserDefaults; no new engine
configuration fields or environment variables were added.

The self-contained local test build is
`build/GuidedSetupLive/JiSpr Guided Setup.app`, with bundle identifier
`com.acrobat.jispr.guidedsetup`. It has separate first-run completion preferences
and normal macOS permission entries. Quit another JiSpr version before starting
its live test, since the engine uses one shared-instance lock for the selected
data directory. Building this app does not replace the saved Living Caret or
Teach Me Once test bundles.

## Download and cache behavior

Model definitions pin public Hugging Face revisions, file sizes, and SHA-256 or
Git blob digests obtained from the official Hub metadata. Downloads contain
model artifacts only; user audio and text are never uploaded.

Inventory is offline and imports no ASR model runtime. Compatible existing
Hugging Face snapshots can be reused, including older snapshots whose required
artifacts match the pinned content. Verification happens on a background worker,
so checking multi-gigabyte weights does not block bridge commands.

New downloads are staged beneath `data_dir/models/.downloads/`. A completed
model is published atomically under `data_dir/models/<catalog-id>/` with a
validated manifest and file stamps. Incomplete, truncated, or corrupt files are
not offered as ready. A failed or cancelled operation leaves other installed
models intact. A corrupt shared-cache candidate can be repaired with a new
managed copy without modifying or deleting the shared cache.

Only one operation runs at a time. Jobs have unique IDs; stale cancel requests,
late completion, and a host exit cannot mark a cancelled operation ready.
Network timeouts and chunk-level cancellation bound normal cancellation waits.
Retries restart a cancelled network transfer; resumable range downloads and
automatic model updates are not included in this version.

## Validation

Validation passed:

- **1,432 Python tests**, including 30 download-domain and 22 guided host/config
  tests covering integrity/cancellation, offline inventory, cache reuse and
  repair, atomic configuration, environment locks, and failed-save recovery.
- **126 native tests**, including 29 new setup cases covering stale replies,
  download progress/cancellation, language compatibility, permissions, practice
  gating, and actual offscreen AppKit previews of all five stages in light/dark.
- Native build, Ruff, mocked demo, and `git diff --check`.
- Real HTTPS download of a pinned 266-byte model configuration into an isolated
  fixture, including digest verification, atomic publication, and ready lookup.
- HTTPS HEAD checks of all five pinned weight URLs, including approved CDN
  redirects and byte-size headers. No model weights were downloaded.
- Visual inspection of the setup previews, including the fixed footer's
  Download/Verify/Cancel/Apply actions.

```sh
uv run pytest
uv run ruff check .
uv run local-flow demo
xcodegen generate --spec macos/JiSpr/project.yml
xcodebuild -project macos/JiSpr/JiSpr.xcodeproj -scheme JiSpr \
  -configuration Debug -derivedDataPath build/JiSprDerivedData \
  -parallel-testing-enabled NO test
git diff --check
```

## Manual acceptance — pending

1. Open a fresh named test build. Confirm Welcome appears and no model download
   or microphone capture starts before an explicit action.
2. Choose a language and confirm the suggested model supports it. Browse cards;
   verify the saved configuration remains unchanged.
3. Download a model. Confirm the actual byte counter advances; cancel and retry.
   A partial model must remain unavailable for Apply. Test network failure and
   retry while a previously working model remains usable.
4. Verify an already-cached compatible model without network access. Confirm
   verification is shown separately from a network transfer.
5. Apply the selection. Confirm existing writing style, Living Caret placement,
   and personalization remain. Check an environment-locked setting reports a
   clear error rather than pretending to save.
6. Complete the three normal macOS permission requests. Start practice and
   dictate into its field. Confirm recording, real text insertion, and Finish
   gating; test both the configured shortcut and hands-free guidance if used.
7. Finish, quit, and reopen: setup should stay completed and normal dictation
   should start. Separately test Later, reopening the guide, and closing during
   a download. Check Living Caret and Teach Me Once still behave as before.

These hardware and network checks are separate from automated validation.
Ad-hoc rebuilt app bundles have a new signing identity and can need fresh macOS
permission grants; guided setup uses normal OS authorization and does not grant
or reset permissions automatically.
