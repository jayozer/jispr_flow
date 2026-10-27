# Teach Me Once v1 — live QA

Date: 2026-10-27 (America/Los_Angeles). Status: in progress.

Test branch: `codex/teach-me-once`, initially tested at source revision `f52bc37`,
stacked on `codex/living-caret` at `fae6ce7`. The scope repair remains on the
Teach Me Once branch; Living Caret is unchanged.

Test app: `build/TeachMeOnceLive/JiSpr Teach Me Once.app`, bundle identifier
`com.acrobat.jispr.teachmeonce`. The Living Caret test app was quit gracefully
before opening this build. The packaged signature and isolated embedded-engine
bridge smoke check passed; the native app and bundled host were verified running.

The temporary manual-test phrase is "The copper lantern is ready", with the
explicit correction `lantern` → `moonlamp`. Using a concrete noun pair keeps
polish from normalizing the source phrase before the learning check.

## User-confirmed results

- General: Ready; Accessibility and Input Monitoring both Granted.
- TextEdit baseline: one insertion containing `lantern`; Living Caret appears
  beside the text cursor while recording and hides afterward.
- Review: identifies TextEdit and previews `lantern` → `moonlamp` with This app
  selected, before Remember is used.
- App scope: **unexpected result under investigation**. After Remember with
  This app selected, the user reported `moonlamp` in Safari as well. A targeted
  read of the synthetic test rule confirmed its stored `app_id` is
  `com.apple.TextEdit`, not global. Scope acceptance remains pending.
  Synthetic test history recorded `lantern` as input and `moonlamp` as output
  with one replacement in both apps. A controlled foreground-app probe then
  reproduced the cause: a long-lived Python process's `NSWorkspace` kept its
  initial app identity across TextEdit → Safari → TextEdit activation, because
  the Python host does not pump Cocoa's main event loop. The first repair used
  a fresh Accessibility focused-application query and mapped its process ID to
  stable app identity, with an empty result on lookup failure.

## Scope-repair validation

- 1,375 headless Python tests passed, including 55 context-adapter tests.
- Ruff, mocked demo, and `git diff --check` passed.
- The native UI is unchanged from the previously tested 97-test build.
- A fresh packaged-app scope check is still required; the original unexpected
  result above is not counted as a pass.
- After the scope-repair app was signed and reopened, macOS retained enabled
  privacy entries for the previous ad-hoc build. Targeted TCC diagnostics
  confirmed a code-requirement mismatch (old vs new app signature) and denied
  access for the updated process. The signed bundle itself verifies correctly.
  Recovery uses normal System Settings removal/re-addition for this test app;
  its app bundle is frozen while the refreshed grants are tested. No TCC
  database edits or unrelated JiSpr permission changes are involved.
  The user then added the current app in Accessibility, replaced/enabled its
  Input Monitoring entry, and toggled its Microphone entry off/on while quit.
  The same frozen scope-repair bundle was reopened after those actions.
- The user then reported `lantern` in both apps. Synthetic test history confirmed
  zero learned replacements, while the saved TextEdit-only rule still produced
  the correct result when the app ID was supplied directly. The first repair
  therefore did not establish live app-scope acceptance.

## Foreground-process repair

The second repair uses the public `GetFrontProcess` / `GetProcessPID` APIs and
maps that PID with `NSRunningApplication`. It avoids Accessibility focus access
and the stale `NSWorkspace` property. These APIs are deprecated since macOS 10.9
but remain public and available in the macOS 27 SDK; the adapter isolates that
compatibility tradeoff and returns empty identity on any failure.

A real background-thread probe with no Cocoa event loop activated TextEdit,
Safari, then TextEdit without changing document text. All three Process Manager
lookups returned status zero and the correct activated app, while the cached
workspace value stayed on its initial app. This validates app switching on the
current Mac; packaged dictation acceptance still requires the user retest.

The production `MacFrontmostApp` adapter and a temporary TextEdit-scoped
`CorrectionStore` rule were also exercised together on that worker thread.
Across the same three real app switches, the detected IDs were correct and the
resulting words were `moonlamp`, `lantern`, `moonlamp`, respectively. Only a
temporary fixture store was used; saved user rules and documents were unchanged.

Latest validation: 1,380 headless Python tests passed, plus Ruff, the mocked
demo, and `git diff --check`. The native UI remains unchanged from its 97-test
build.

## Remaining acceptance checks

- This app applies in TextEdit and does not apply in Safari.
- Undo last learn restores the original wording.
- Everywhere applies in both apps.
- Edited drafts survive a newer dictation; Load latest changes records explicitly.
- Copy corrected text and appearance remain usable.
- Full app restart clears in-memory reviews while preserving remembered rules.
- Forget removes the correction; Undo last forget restores it.
- Remove only the temporary test rule; confirm Living Caret still works.

Live observations above are separate from the automated checks documented in
[Teach Me Once v1](teach-me-once-v1.md).
