import XCTest
@testable import JiSpr

final class FloatingPillControllerTests: XCTestCase {
    private func config(
        state: DictationState,
        enabled: Bool = true,
        audioLevel: Double = 0,
        position: String = "bottom"
    ) -> FloatingPillConfiguration {
        FloatingPillConfiguration(
            enabled: enabled,
            style: "compact",
            state: state,
            detail: "",
            audioLevel: audioLevel,
            hotkey: "fn",
            position: position
        )
    }

    func testFirstIdleAfterInsertedSchedulesTheDelayedTransition() {
        XCTAssertEqual(
            FloatingPillController.transition(
                for: config(state: .idle),
                displayed: config(state: .inserted),
                idlePending: false
            ),
            .delayIdle
        )
    }

    func testAudioLevelRefreshesDoNotRestartAPendingIdleTransition() {
        // Hands-free mode repeats idle updates on every audio-level frame
        // (~30ms apart). Each one must coalesce into the already-scheduled
        // transition; re-arming the 0.9s timer per frame postpones the
        // return to idle forever and the pill stays stuck on "Inserted".
        XCTAssertEqual(
            FloatingPillController.transition(
                for: config(state: .idle, audioLevel: 0.2),
                displayed: config(state: .inserted),
                idlePending: true
            ),
            .coalesceIdle
        )
    }

    func testWarningAndErrorStatesAlsoHoldBeforeReturningToIdle() {
        for held in [DictationState.warning, .error] {
            XCTAssertEqual(
                FloatingPillController.transition(
                    for: config(state: .idle),
                    displayed: config(state: held),
                    idlePending: false
                ),
                .delayIdle
            )
            XCTAssertEqual(
                FloatingPillController.transition(
                    for: config(state: .idle),
                    displayed: config(state: held),
                    idlePending: true
                ),
                .coalesceIdle
            )
        }
    }

    func testNewStateCancelsThePendingIdleTransition() {
        XCTAssertEqual(
            FloatingPillController.transition(
                for: config(state: .recording),
                displayed: config(state: .inserted),
                idlePending: true
            ),
            .apply
        )
    }

    func testIdleOverIdleAppliesDirectly() {
        XCTAssertEqual(
            FloatingPillController.transition(
                for: config(state: .idle),
                displayed: config(state: .idle),
                idlePending: false
            ),
            .apply
        )
    }

    func testDisabledHidesRegardlessOfPendingTransition() {
        XCTAssertEqual(
            FloatingPillController.transition(
                for: config(state: .idle, enabled: false),
                displayed: config(state: .inserted),
                idlePending: true
            ),
            .hide
        )
    }

    func testLivingCaretHidesAtRestButKeepsErrorsVisible() {
        for state in [DictationState.idle, .offline] {
            XCTAssertTrue(FloatingPillController.hidesAtRest(config(state: state, position: "caret")))
            XCTAssertFalse(FloatingPillController.hidesAtRest(config(state: state)))
        }
        for state in [DictationState.recording, .preview, .processing, .inserted, .warning, .error] {
            XCTAssertFalse(FloatingPillController.hidesAtRest(config(state: state, position: "caret")))
        }
    }

    func testLivingCaretSuccessRetainsTheConfirmationBeforeIdleHidesIt() {
        XCTAssertEqual(
            FloatingPillController.transition(
                for: config(state: .idle, position: "caret"),
                displayed: config(state: .inserted, position: "caret"),
                idlePending: false
            ),
            .delayIdle
        )
        XCTAssertEqual(
            FloatingPillController.transition(
                for: config(state: .recording, position: "caret"),
                displayed: config(state: .inserted, position: "caret"),
                idlePending: true
            ),
            .apply
        )
    }

    func testSwitchingPlacementImmediatelyOverridesAPendingConfirmation() {
        XCTAssertEqual(
            FloatingPillController.transition(
                for: config(state: .idle, position: "caret"),
                displayed: config(state: .inserted),
                idlePending: true
            ),
            .apply
        )
    }

    func testOnlyActiveAndSuccessfulStatesUseTheSmallCaretSurface() {
        for state in [DictationState.recording, .preview, .processing, .inserted] {
            let configuration = config(state: state, position: "caret")
            XCTAssertTrue(configuration.showsLivingCaret)
            let presentation = FloatingPillPresentation(configuration: configuration)
            XCTAssertEqual(presentation.width, 72)
            XCTAssertEqual(presentation.height, 28)
        }
        for state in [DictationState.warning, .error] {
            let configuration = config(state: state, position: "caret")
            XCTAssertFalse(configuration.showsLivingCaret)
            XCTAssertEqual(FloatingPillPresentation(configuration: configuration).width, 280)
        }
    }

    @MainActor
    func testStatusPanelCannotTakeKeyboardFocus() {
        let panel = DictationStatusPanel(
            contentRect: .zero,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: true
        )
        XCTAssertFalse(panel.canBecomeKey)
        XCTAssertFalse(panel.canBecomeMain)
        panel.close()
    }
}
