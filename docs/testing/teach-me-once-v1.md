# Teach Me Once v1

Implementation date: 2026-10-27 (America/Los_Angeles).

## Branch stack

- `codex/living-caret` at `fae6ce7`: committed, live-validated Living Caret base,
  including its separately tested diagnostic-warning fixes.
- `codex/teach-me-once`: this experiment, branched from that exact commit.

To return to the base after saving any unrelated local edits:

```sh
git switch codex/living-caret
```

Rebuild the selected branch when changing app versions; switching Git branches
does not change an already running app bundle. A later approved feature can be
stacked on the Teach Me Once commit. No remote branch or PR is required for this
local experiment.

The self-contained local test app is
`build/TeachMeOnceLive/JiSpr Teach Me Once.app`, with identifier
`com.acrobat.jispr.teachmeonce`. Quit the Living Caret app before opening it;
the two versions share the engine's single-instance lock. The test app uses
normal macOS permission prompts under its own distinct name. It has not yet
completed the manual acceptance checklist below.

## First-version behavior

1. Dictate into an app, then open **JiSpr → Teach Me Once…**.
2. Correct one word or phrase in JiSpr's own review editor.
3. Choose **Review Changes** to inspect the suggested phrase replacements.
4. Choose **This app** or **Everywhere**, then explicitly **Remember** a phrase.
5. The next matching dictation uses that exact replacement. Undo and Forget are
   available in the same window.

This is explicit correction learning. It does not monitor post-insertion edits
in other apps, infer broad writing-style policies, or automatically replace
previously inserted text. **Copy corrected text** is an explicit clipboard action.

App identity is resolved near insertion even when per-app styles are disabled
or no app-style rules exist. An unavailable identity leaves global scope as the
only choice. App-scoped rules take precedence over global rules for the same
source. Matching is case-insensitive with word boundaries; replacement text is
literal and cannot invoke spoken commands. Rules run after transforms and are
skipped in verbatim cleanup mode.

## Data and failure behavior

- `corrections.json` is a separate, versioned, atomic local store. It is created
  on the first remembered correction, and is not used by the Living Caret base.
  Learning does not edit dictionaries, snippets, or configuration files.
- The host retains at most 20 review records, each at most 32,000 characters.
  Diff previews are limited to 1,024 word/identifier tokens per version so a
  repetitive long passage cannot block engine commands.
  Reviews and single-use Undo tokens are session-local and independent of
  history recording. A stale/evicted review produces an actionable error.
- A new dictation does not overwrite an edited review. **Load latest** explicitly
  changes which record is being reviewed. Late replies/errors are discarded.
- Each learn is validated against the original cached text and the edited
  version. Fabricated, ambiguous, deletion-only, or number-only changes cannot
  be remembered through the review protocol.
- Undo compares the current stored rule with the saved change before restoring
  it. A newer edit wins. Forget can also be undone while that token is current.
- Invalid correction files are left intact; dictation continues without those
  replacements and reports a warning. The review window also reports the error.
- A review event follows successful text output before later key actions or
  history writes. Review failures never retry or discard the output. Existing
  clipboard fallback is still possible, so the UI calls this a dictation rather
  than a guarantee of insertion into the original app.

## Automated checks

The implementation includes domain, host/pipeline integration, native state and
decoding tests, and offscreen AppKit light/dark review previews with pixel checks
that reject blank captures. Validation passed:

- **1,356 Python tests**, including 64 correction-domain and 24 bridge/pipeline
  integration cases.
- **97 native tests**, including backward-compatible decoding, stale replies,
  null/latest records, edited-draft preservation, and Undo state.
- Ruff, mocked demo, and `git diff --check`.
- Native build and visual inspection of both review appearances.

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

## Manual acceptance checklist — pending

These checks require the native app, real dictation, and user approval of learned
rules. Automated results do not establish live acceptance.

1. Dictate "We utilize this approach" into TextEdit. Open Teach Me Once and edit
   "utilize" to "use". Review Changes should propose that pair without saving it.
2. Remember the pair for **This app**. Dictate the original phrase again into
   TextEdit; confirm "use" appears. Dictate it into Safari; confirm the TextEdit
   rule does not affect Safari.
3. Undo the learn. Dictate again and confirm the previous wording returns.
4. Remember a pair for **Everywhere**. Confirm it applies across both apps, then
   Forget it and Undo the Forget. Remove test rules when finished.
5. Leave an edited review open and dictate a new sentence elsewhere. Confirm the
   edited draft stays intact and Load latest switches to the new record.
6. Check the zero state after a full engine-host restart, and rule persistence
   across that restart. An ordinary Restart Engine reloads the dictation session;
   the host keeps in-memory reviews until the app/host process exits.
7. Confirm Copy corrected text works, both visual appearances remain readable,
   and Living Caret still records/inserts/hides normally.

Use the completed [Living Caret live QA](living-caret-v1-live-qa.md) as the baseline
when deciding whether to keep this stacked experiment.
