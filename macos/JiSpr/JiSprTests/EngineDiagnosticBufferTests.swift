import XCTest
@testable import JiSpr

final class EngineDiagnosticBufferTests: XCTestCase {
    func testSplitHotkeyReadySignalIsDeliveredAsOneDiagnostic() {
        var buffer = EngineDiagnosticBuffer()

        XCTAssertEqual(buffer.append(Data("Fn hotkey listener ".utf8)), [])
        XCTAssertEqual(buffer.append(Data("active.".utf8)), [])
        XCTAssertEqual(buffer.append(Data("\n".utf8)), ["Fn hotkey listener active."])
    }

    func testCoalescedDiagnosticsKeepTheirOrderAndIgnoreBlankLines() {
        var buffer = EngineDiagnosticBuffer()

        XCTAssertEqual(
            buffer.append(Data("  \r\nCould not create the macOS event tap.\r\n\nFn hotkey listener active.\n".utf8)),
            ["Could not create the macOS event tap.", "Fn hotkey listener active."]
        )
    }

    func testUTF8CharacterSplitAcrossReadsIsPreserved() {
        var buffer = EngineDiagnosticBuffer()
        let prefix = Data("Ready — ".utf8)
        let split = prefix.count - 2

        XCTAssertEqual(buffer.append(Data(prefix.prefix(split))), [])
        XCTAssertEqual(
            buffer.append(Data(prefix.dropFirst(split)) + Data("世界\n".utf8)),
            ["Ready — 世界"]
        )
    }

    func testEOFEmitsUnterminatedLineOnce() {
        var buffer = EngineDiagnosticBuffer()

        XCTAssertEqual(buffer.append(Data("engine stopped without newline".utf8)), [])
        XCTAssertEqual(buffer.finish(), ["engine stopped without newline"])
        XCTAssertEqual(buffer.finish(), [])
    }

    func testResetDoesNotCarryAnOldProcessFragmentIntoTheNextLaunch() {
        var buffer = EngineDiagnosticBuffer()

        _ = buffer.append(Data("stale permission ".utf8))
        buffer.reset()

        XCTAssertEqual(
            buffer.append(Data("Fn hotkey listener active.\n".utf8)),
            ["Fn hotkey listener active."]
        )
    }

    func testOversizedLineIsTruncatedWithoutLosingTheFollowingReadySignal() {
        var buffer = EngineDiagnosticBuffer(maximumLineBytes: 32)

        XCTAssertEqual(buffer.append(Data(repeating: 0x61, count: 1_000_000)), [])
        XCTAssertEqual(
            buffer.append(Data("\nFn hotkey listener active.\n".utf8)),
            [String(repeating: "a", count: 32) + " [diagnostic truncated]", "Fn hotkey listener active."]
        )
        XCTAssertEqual(buffer.finish(), [])
    }

    func testOversizedUnterminatedLineFlushesAtEOF() {
        var buffer = EngineDiagnosticBuffer(maximumLineBytes: 4)

        _ = buffer.append(Data("abcdef".utf8))

        XCTAssertEqual(buffer.finish(), ["abcd [diagnostic truncated]"])
        XCTAssertEqual(buffer.append(Data("ok\n".utf8)), ["ok"])
    }
}
