# Living Caret v1 live QA

Date: 2026-10-27 (America/Los_Angeles). Branch: `codex/voice-experience`.

The user performed these checks on their Mac and reported the results in chat.
The test app used the branch's native UI and Python package with an embedded
Python runtime. Its frozen local identity was `com.acrobat.jispr.livingcaret`
(display name **JiSpr Living Caret**). This was a local test build.

## Observed results

| Check | Result |
| --- | --- |
| TextEdit dictation | Pass: indicator beside the caret; text inserted once. |
| Safari standard text field | Pass: caret placement, one insertion, and hiding afterward. |
| Bottom/right screen edges | Pass: indicator remains visible and leaves the text line clear. |
| Esc cancellation | Pass: canceled utterance adds no text. |
| Recording after cancellation | Pass: next utterance inserts once. |
| Compact and expanded styles | Pass: both work beside the caret and hide while idle. |
| Second monitor | Pass: user reports correct positioning without issues. |
| ChatGPT window | Bottom fallback appears during dictation and hides afterward. The exact reason caret bounds are unavailable was not established. |
| Pause, resume, and Restart Engine | Pass: subsequent TextEdit dictation inserts once, follows the caret, and hides afterward. |
| Settings retained | `floating_pill = true`, `pill_position = "caret"`, `pill_style = "compact"`. |

The user explicitly prefers Living Caret hidden while idle and accepts the
existing persistent idle bar for **Bottom of Screen**. Keep that distinction.

## Permission and warning findings

The external-engine Debug build initially reported permission failures. A
self-contained test bundle was prepared. The installed and test copies shared
an app identifier but had different ad-hoc designated requirements, so the test
bundle received a distinct identity and the user granted access to that app.

After a full reopen, macOS logs reported Accessibility and Input Monitoring as
allowed. An event-tap metadata query independently showed an enabled session
tap in the embedded engine with the Fn/key-down mask. The user then confirmed
working live dictation despite a lingering Fn warning.

Source review found two ways successful listener diagnostics could fail to
clear stale feedback: stderr reads could split a line, and the start reply could
change the UI state before the successful-listener diagnostic cleared its banner.
The branch now frames stderr lines, flushes trailing diagnostics at process exit,
and clears only Fn-specific feedback when the listener reports success. These
fixes preserve unrelated errors and current dictation states.

The running live-test bundle was left unchanged during the interactive checks
to preserve its granted identity. The warning fixes are verified separately by
the automated regression suite; they are not in that frozen live-test bundle.

## Automated validation

- Python suite: 1,268 passed.
- Ruff, mocked demo, and `git diff --check`: passed.
- Initial native suite: 52 passed, including geometry, cancellation, state
  transitions, permission degradation, and light/dark view rendering.
- Targeted warning regressions: 16 passed, including a real child-process
  diagnostic/termination ordering check.
- Full native suite with warning regressions: **68 passed, zero failures**.

Reproduce the full native suite with:

```sh
xcodegen generate --spec macos/JiSpr/project.yml
xcodebuild -project macos/JiSpr/JiSpr.xcodeproj -scheme JiSpr \
  -configuration Debug -derivedDataPath build/JiSprDerivedData \
  -parallel-testing-enabled NO test
```

The general [manual checklist](../../README.md#living-caret-manual-checks)
remains available for future builds. Permission-denied behavior is covered by
automated tests; working macOS grants were not revoked during this live session.
