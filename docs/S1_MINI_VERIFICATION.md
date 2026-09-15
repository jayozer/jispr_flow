# S1-mini integration verification

Date: 2026-09-14. Local development verification; no microphone recording,
real transcript, or external inference service was used in these checks.

## Automated checks

- PASS: 1,227 Python tests; 27 skipped in the isolated environment.
  Command: `UV_CACHE_DIR=/private/tmp/jispr-uv-cache uv run --isolated --extra desktop --extra audio --group dev --with numpy pytest --tb=short`.
- PASS: `uv run --no-sync ruff check .` and `git diff --check`.
- PASS: `uv run --no-sync local-flow demo` (mock ASR/LLM and fake insertion).
- PASS: native Debug build with `xcodebuild -project macos/JiSpr/JiSpr.xcodeproj -scheme JiSpr -configuration Debug -derivedDataPath build/JiSprDerivedData build`.

The initial full-suite attempt in the existing all-extras environment aborted
inside MLX import under the sandbox. The isolated run excludes the MLX runtime;
adapter tests use mocks. A pre-existing scratchpad wiring test was corrected
to stub microphone construction rather than require a real input device.
Xcode required normal access outside the sandbox for Swift macro compilation.

`tests/test_s1_mini.py` covers the fixed protocol, raw correction context,
built-in styles, language switching, command/snippet preservation, dictionary
terms, blank and unsafe output, input limits, chunk failure, commands created
across chunk boundaries, unavailable models, timeouts, malformed responses,
and switching between S1-mini and general-purpose models. It also verifies
that general transforms cannot send their prompts to S1-mini.

## Live local model checks

The existing `s1-mini` model exposed by LM Studio at localhost:1234 was used
with synthetic text, a temporary personalization directory, and a fake text
sink. No new model weights were downloaded. These are integration smoke
checks, not an accuracy benchmark or microphone/insertion acceptance test.

| Case | Observed result |
| --- | --- |
| Correction from Friday to Thursday | `So send the report by Thursday.` |
| Spoken amount | `The total is $42.50.` |
| Dictated question | `Can you send me the report tomorrow?` |
| `um uh hmm` | Empty output, no insertion |
| Email | Greeting, body, and sign-off on separate paragraphs |
| Spoken new paragraph and press enter | Rules preserved formatting and a fake Enter action |
| Dictionary spelling | `Please open PostgreSQL.` |

All seven cases behaved as expected. Successful model requests used the raw
completion endpoint with the explicit non-thinking prefix. Field context was
not included. The command case intentionally used rules without inference.

## Installed app verification

- PASS: `JISPR_BUILD_NUMBER=2026091401 JISPR_NOTARY_PROFILE= ./script/package_beta.sh`.
  Signed with the existing Developer ID identity; no notarization submission.
- PASS: the staged app's embedded Python imported the bundled `s1_mini.py`
  (outside the repository working directory) and produced `The total is $42.50.`
  through the local model with `used_llm=True`.
- Installed `/Applications/JiSpr.app`, version 0.1.2, build **2026091401**.
  Previous app retained at `/private/tmp/jispr-before-s1.EY9tWu/Original-JiSpr.app`.
- PASS: `codesign --verify --deep --strict /Applications/JiSpr.app` after launch.
- PASS: native UI showed **Ready**, existing Accessibility/Input Monitoring
  grants, Parakeet v3 with English selected, and **S1-mini by Superwhisper
  (s1-mini)** selected for polish after Refresh. LM Studio reported six models.
  The new explanation appeared and was inspected in the native settings UI.

The generated DMG is signed but not notarized; this run validates a local
installation, not readiness for external distribution.

## PR review follow-up

The review identified a regression for filler-only input: resolving an
auto-selected model could wait for LM Studio even after rules removed all
text. A nine-case regression test reproduced the unwanted requests before
the fix (six failed, three passed). Empty rule results now return before
model resolution or inference, including when S1-mini is explicitly selected.

- PASS: full isolated suite using the command above: **1,236 passed, 27 skipped**.
- PASS: Ruff, mocked demo, and `git diff --check`.
- All nine new cases make zero HTTP requests and return no text or warnings.

This follow-up updates the PR source; the installed build described above
predates this early-return fix and was not repackaged during review.

## Manual acceptance still needed

- Dictate through the physical microphone and assess latency/quality for the
  user's voice, vocabulary, and destinations.
- Try English dictation with each desired ASR model. The live checks above
  start from text; they do not measure Whisper or Parakeet recognition.
- Check real insertion into target apps. All synthetic checks used a fake sink.

See the [README](../README.md#s1-mini-by-superwhisper-english-writing-polish)
for selection steps and supported features. S1-mini remains an English
normalizer; select a general-purpose model for custom rewriting.
