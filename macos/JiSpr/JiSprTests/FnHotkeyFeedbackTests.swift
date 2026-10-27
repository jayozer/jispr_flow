import XCTest
@testable import JiSpr

final class FnHotkeyFeedbackTests: XCTestCase {
    private let pausedBanner = "Fn hotkey is paused. Open General → Permissions, grant JiSpr "
        + "Accessibility and Input Monitoring, then restart the engine."
    private let grantedBanner = "Permissions granted—restart the JiSpr engine to enable Fn."

    func testStartReplyMaySetIdleBeforeTheStalePermissionBannerClears() {
        for banner in [pausedBanner, grantedBanner] {
            let result = FnHotkeyFeedback.listenerActivated(
                state: .idle, detail: "Ready for your hotkey", banner: banner
            )
            XCTAssertEqual(result.state, .idle)
            XCTAssertEqual(result.detail, "Ready for your hotkey")
            XCTAssertNil(result.banner)
        }
    }

    func testActiveRecordingAndProcessingKeepTheirStateAndDetail() {
        for state in [DictationState.recording, .preview, .processing, .inserted] {
            let result = FnHotkeyFeedback.listenerActivated(
                state: state, detail: "Current dictation feedback", banner: pausedBanner
            )
            XCTAssertEqual(result.state, state)
            XCTAssertEqual(result.detail, "Current dictation feedback")
            XCTAssertNil(result.banner)
        }
    }

    func testGenuineFnPermissionWarningReturnsToReady() {
        let result = FnHotkeyFeedback.listenerActivated(
            state: .warning, detail: "Fn hotkey needs permission", banner: pausedBanner
        )
        XCTAssertEqual(result.state, .idle)
        XCTAssertEqual(result.detail, "Ready for your hotkey")
        XCTAssertNil(result.banner)
    }

    func testDeveloperBuildFnPermissionBannerAlsoClears() {
        let result = FnHotkeyFeedback.listenerActivated(
            state: .idle,
            detail: "Ready for your hotkey",
            banner: "Fn hotkey is paused. This developer build runs the engine outside JiSpr.app."
        )
        XCTAssertNil(result.banner)
    }

    func testUnrelatedWarningAndBannerArePreserved() {
        let result = FnHotkeyFeedback.listenerActivated(
            state: .warning,
            detail: "Microphone needs permission",
            banner: "Grant microphone access before dictating."
        )
        XCTAssertEqual(result.state, .warning)
        XCTAssertEqual(result.detail, "Microphone needs permission")
        XCTAssertEqual(result.banner, "Grant microphone access before dictating.")
    }

    func testUnrelatedErrorStateAndBannerArePreserved() {
        let result = FnHotkeyFeedback.listenerActivated(
            state: .error,
            detail: "Local engine stopped",
            banner: "The local engine stopped. Choose Restart Engine."
        )
        XCTAssertEqual(result.state, .error)
        XCTAssertEqual(result.detail, "Local engine stopped")
        XCTAssertEqual(result.banner, "The local engine stopped. Choose Restart Engine.")
    }

    func testClearingFnWarningDoesNotClearAnUnrelatedErrorBanner() {
        let result = FnHotkeyFeedback.listenerActivated(
            state: .warning,
            detail: "Fn hotkey needs permission",
            banner: "LM Studio connection failed."
        )
        XCTAssertEqual(result.state, .idle)
        XCTAssertEqual(result.detail, "Ready for your hotkey")
        XCTAssertEqual(result.banner, "LM Studio connection failed.")
    }

    func testOnlyTheFnWarningStateIsResetEvenWhenDetailMatches() {
        let result = FnHotkeyFeedback.listenerActivated(
            state: .error, detail: "Fn hotkey needs permission", banner: grantedBanner
        )
        XCTAssertEqual(result.state, .error)
        XCTAssertEqual(result.detail, "Fn hotkey needs permission")
        XCTAssertNil(result.banner)
    }
}
