import ApplicationServices
import Foundation
import XCTest
@testable import JiSpr

final class CaretBoundsProviderTests: XCTestCase {
    func testMissingPermissionDoesNotLookUpTheFrontmostAppOrReadGeometry() async {
        let calls = Calls()
        let provider = AccessibilityCaretBoundsProvider(
            isTrusted: { false },
            frontmostProcessID: { calls.record(); return 123 },
            boundsForProcess: { _ in calls.record(); return .zero }
        )
        let bounds = await provider.currentBounds()
        XCTAssertNil(bounds)
        XCTAssertEqual(calls.count, 0)
    }

    func testMissingAndInvalidFrontmostPIDDoNotReadGeometry() async {
        for processID in [nil, 0, -1] as [pid_t?] {
            let calls = Calls()
            let provider = AccessibilityCaretBoundsProvider(
                isTrusted: { true },
                frontmostProcessID: { processID },
                boundsForProcess: { _ in calls.record(); return .zero }
            )
            let bounds = await provider.currentBounds()
            XCTAssertNil(bounds)
            XCTAssertEqual(calls.count, 0)
        }
    }

    @MainActor
    func testAXReaderRunsOffTheMainThreadAndPreservesGlobalCoordinates() async {
        let expected = CGRect(x: -1800, y: -400, width: 0, height: 20)
        let calls = Calls()
        let provider = AccessibilityCaretBoundsProvider(
            isTrusted: { true },
            frontmostProcessID: { 123 },
            boundsForProcess: { processID in
                calls.record(wasMainThread: Thread.isMainThread)
                XCTAssertEqual(processID, 123)
                return expected
            }
        )
        let bounds = await provider.currentBounds()
        XCTAssertEqual(bounds, expected)
        XCTAssertEqual(calls.count, 1)
        XCTAssertFalse(calls.wasMainThread)
    }

    func testUnavailableAndUntrustworthyGeometryReturnNil() async {
        for rect in [
            nil,
            .zero,
            CGRect(x: CGFloat.nan, y: 100, width: 1, height: 20),
            CGRect(x: 100, y: 100, width: 1, height: 1000),
        ] as [CGRect?] {
            let provider = AccessibilityCaretBoundsProvider(
                isTrusted: { true }, frontmostProcessID: { 123 },
                boundsForProcess: { _ in rect }
            )
            let bounds = await provider.currentBounds()
            XCTAssertNil(bounds)
        }
    }

    func testInsertionRangeCollapsesAtTheEndWithoutReadingSelectedText() {
        let insertion = AccessibilityCaretBoundsProvider.insertionRange(
            for: CFRange(location: 12, length: 8)
        )
        XCTAssertEqual(insertion?.location, 20)
        XCTAssertEqual(insertion?.length, 0)
        let empty = AccessibilityCaretBoundsProvider.insertionRange(
            for: CFRange(location: 0, length: 0)
        )
        XCTAssertEqual(empty?.location, 0)
        XCTAssertEqual(empty?.length, 0)
    }

    func testNegativeAndOverflowingSelectionMetadataReturnNil() {
        for selection in [
            CFRange(location: -1, length: 0),
            CFRange(location: 0, length: -1),
            CFRange(location: Int.max, length: 1),
        ] {
            XCTAssertNil(AccessibilityCaretBoundsProvider.insertionRange(for: selection))
        }
    }

    func testSecureAndUnsupportedFieldsAreRejected() {
        for subrole in ["AXSecureTextField", "AXPasswordField", "secureCustomField"] {
            XCTAssertFalse(AccessibilityCaretBoundsProvider.supportsGeometry(
                role: "AXTextField", subrole: subrole
            ))
        }
        XCTAssertFalse(AccessibilityCaretBoundsProvider.supportsGeometry(
            role: nil, subrole: nil
        ))
        XCTAssertFalse(AccessibilityCaretBoundsProvider.supportsGeometry(
            role: "AXButton", subrole: nil
        ))
        for role in ["AXTextField", "AXTextArea", "AXComboBox"] {
            XCTAssertTrue(AccessibilityCaretBoundsProvider.supportsGeometry(
                role: role, subrole: nil
            ))
        }
    }

    private final class Calls: @unchecked Sendable {
        private let lock = NSLock()
        private var storedCount = 0
        private var storedWasMainThread = false

        var count: Int { lock.withLock { storedCount } }
        var wasMainThread: Bool { lock.withLock { storedWasMainThread } }

        func record(wasMainThread: Bool = false) {
            lock.withLock {
                storedCount += 1
                storedWasMainThread = wasMainThread
            }
        }
    }
}
