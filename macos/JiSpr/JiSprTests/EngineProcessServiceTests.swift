import XCTest
@testable import JiSpr

final class EngineProcessServiceTests: XCTestCase {
    func testExitFlushesFinalDiagnosticBeforeTerminationNotification() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("jispr-diagnostics-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let executable = directory.appendingPathComponent("engine")
        try Data("#!/bin/sh\nprintf 'Fn hotkey listener active.' >&2\n".utf8)
            .write(to: executable)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: executable.path
        )
        let events = DiagnosticEvents()
        let terminated = expectation(description: "engine termination")
        let service = EngineProcessService()

        try service.start(
            location: EngineLocation(executableURL: executable, workingDirectoryURL: directory),
            onMessage: { _ in },
            onDiagnostic: { events.append($0) },
            onTermination: { _ in
                events.append("terminated")
                terminated.fulfill()
            }
        )

        wait(for: [terminated], timeout: 5)
        XCTAssertEqual(events.values, ["Fn hotkey listener active.", "terminated"])
        XCTAssertFalse(service.isRunning)
    }
}

private final class DiagnosticEvents: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String] = []

    var values: [String] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    func append(_ value: String) {
        lock.lock()
        defer { lock.unlock() }
        storage.append(value)
    }
}
