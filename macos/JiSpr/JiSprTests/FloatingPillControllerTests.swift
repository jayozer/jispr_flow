import XCTest
@testable import JiSpr

final class FloatingPillControllerTests: XCTestCase {
    private func config(
        state: DictationState,
        enabled: Bool = true,
        audioLevel: Double = 0
    ) -> FloatingPillConfiguration {
        FloatingPillConfiguration(
            enabled: enabled,
            style: "compact",
            state: state,
            detail: "",
            audioLevel: audioLevel,
            hotkey: "fn"
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
}
