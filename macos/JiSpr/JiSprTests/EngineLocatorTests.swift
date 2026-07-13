import XCTest
@testable import JiSpr

final class EngineLocatorTests: XCTestCase {
    @MainActor
    func testFirstActivationRequestsSettingsOnce() {
        let delegate = AppDelegate()
        let initialRequest = AppStore.shared.settingsRequestID
        let notification = Notification(name: NSApplication.didBecomeActiveNotification)

        delegate.applicationDidBecomeActive(notification)
        delegate.applicationDidBecomeActive(notification)

        XCTAssertEqual(AppStore.shared.settingsRequestID, initialRequest + 1)
    }

    @MainActor
    func testReopenRequestsSettingsWindow() {
        let delegate = AppDelegate()
        let initialRequest = AppStore.shared.settingsRequestID

        XCTAssertTrue(
            delegate.applicationShouldHandleReopen(
                NSApplication.shared,
                hasVisibleWindows: false
            )
        )
        XCTAssertEqual(AppStore.shared.settingsRequestID, initialRequest + 1)
    }

    func testEngineEnvironmentIncludesHomebrewAndPreservesExistingPath() {
        let environment = EngineProcessService.launchEnvironment(
            base: ["PATH": "/custom/bin:/usr/bin", "TOKEN": "kept"]
        )

        XCTAssertEqual(
            environment["PATH"],
            "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/custom/bin"
        )
        XCTAssertEqual(environment["TOKEN"], "kept")
    }

    func testEngineEnvironmentDisablesBytecodeCaches() {
        // Regression: the engine used to write __pycache__ into the sealed
        // bundle at runtime, so `codesign --verify --strict` failed on every
        // installed app after its first launch.
        let environment = EngineProcessService.launchEnvironment(base: [:])

        XCTAssertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
    }

    func testEnvironmentOverridesBundleConfiguration() throws {
        let executable = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("jispr-engine-\(UUID().uuidString)")
        FileManager.default.createFile(atPath: executable.path, contents: Data("#!/bin/sh\n".utf8))
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: executable.path
        )
        defer { try? FileManager.default.removeItem(at: executable) }

        let location = try EngineLocator.resolve(
            environment: [
                "JISPR_ENGINE_PATH": executable.path,
                "JISPR_WORKING_DIRECTORY": NSTemporaryDirectory(),
            ],
            bundle: .main
        )

        XCTAssertEqual(location.executableURL.path, executable.path)
        XCTAssertEqual(location.workingDirectoryURL?.path, URL(fileURLWithPath: NSTemporaryDirectory()).path)
    }

    func testMissingExecutableIsActionable() {
        XCTAssertThrowsError(
            try EngineLocator.resolve(
                environment: ["JISPR_ENGINE_PATH": "/definitely/missing/local-flow"],
                bundle: .main
            )
        ) { error in
            XCTAssertTrue(error.localizedDescription.contains("Reinstall JiSpr"))
        }
    }
}
