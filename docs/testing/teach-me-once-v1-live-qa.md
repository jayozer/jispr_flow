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
  the Python host does not pump Cocoa's main event loop. The repair uses a
  fresh Accessibility focused-application query and maps its process ID to
  stable app identity, with an empty result on lookup failure.

## Scope-repair validation

- 1,375 headless Python tests passed, including 55 context-adapter tests.
- Ruff, mocked demo, and `git diff --check` passed.
- The native UI is unchanged from the previously tested 97-test build.
- A fresh packaged-app scope check is still required; the original unexpected
  result above is not counted as a pass.

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
