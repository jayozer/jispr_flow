import XCTest
@testable import JiSpr

private actor SuspendedCaretProvider: CaretBoundsProviding {
    private var requests: [CheckedContinuation<CGRect?, Never>] = []
    private var requestCount = 0
    private let onRequest: @Sendable (Int) -> Void

    init(onRequest: @escaping @Sendable (Int) -> Void) {
        self.onRequest = onRequest
    }

    func currentBounds() async -> CGRect? {
        await withCheckedContinuation { continuation in
            requests.append(continuation)
            requestCount += 1
            onRequest(requestCount)
        }
    }

    func resolveNext(_ bounds: CGRect?) {
        requests.removeFirst().resume(returning: bounds)
    }
}

final class CaretTrackerTests: XCTestCase {
    @MainActor
    func testRepeatedStartUsesASingleOutstandingRequest() async {
        let requested = expectation(description: "One AX request")
        requested.assertForOverFulfill = true
        let provider = SuspendedCaretProvider { _ in requested.fulfill() }
        let tracker = CaretTracker(provider: provider, interval: .seconds(60))
        tracker.start()
        tracker.start()
        await fulfillment(of: [requested], timeout: 1)
        XCTAssertTrue(tracker.isRunning)
        tracker.stop()
        await provider.resolveNext(nil)
        XCTAssertFalse(tracker.isRunning)
    }

    @MainActor
    func testValidBoundsAndUnavailableBoundsReachTheRenderer() async {
        for bounds in [CGRect(x: 100, y: 200, width: 0, height: 20), nil] {
            let requested = expectation(description: "AX request")
            let delivered = expectation(description: "Render update")
            let provider = SuspendedCaretProvider { _ in requested.fulfill() }
            let tracker = CaretTracker(provider: provider, interval: .seconds(60))
            var received: CGRect?
            tracker.onChange = { value in
                received = value
                delivered.fulfill()
            }
            tracker.start()
            await fulfillment(of: [requested], timeout: 1)
            await provider.resolveNext(bounds)
            await fulfillment(of: [delivered], timeout: 1)
            XCTAssertEqual(received, bounds)
            tracker.stop()
        }
    }

    @MainActor
    func testStoppedTrackerDiscardsALateAXResponse() async {
        let requested = expectation(description: "AX request")
        let delivered = expectation(description: "Late response must be discarded")
        delivered.isInverted = true
        let provider = SuspendedCaretProvider { _ in requested.fulfill() }
        let tracker = CaretTracker(provider: provider, interval: .seconds(60))
        tracker.onChange = { _ in delivered.fulfill() }
        tracker.start()
        await fulfillment(of: [requested], timeout: 1)
        tracker.stop()
        await provider.resolveNext(CGRect(x: 0, y: 0, width: 1, height: 20))
        await fulfillment(of: [delivered], timeout: 0.05)
        XCTAssertFalse(tracker.isRunning)
    }

    @MainActor
    func testRestartDiscardsThePreviousSessionAndAcceptsTheNewOne() async {
        let requested = expectation(description: "Both sessions requested AX")
        requested.expectedFulfillmentCount = 2
        let delivered = expectation(description: "Only the new session renders")
        delivered.assertForOverFulfill = true
        let firstRequested = expectation(description: "First session started")
        let provider = SuspendedCaretProvider { count in
            requested.fulfill()
            if count == 1 { firstRequested.fulfill() }
        }
        let tracker = CaretTracker(provider: provider, interval: .seconds(60))
        var received: [CGRect?] = []
        tracker.onChange = { value in
            received.append(value)
            delivered.fulfill()
        }
        tracker.start()
        // Let the first task reach the provider before replacing it.
        await fulfillment(of: [firstRequested], timeout: 1)
        tracker.stop()
        tracker.start()
        await fulfillment(of: [requested], timeout: 1)
        await provider.resolveNext(CGRect(x: 1, y: 1, width: 1, height: 20))
        let current = CGRect(x: 300, y: 400, width: 0, height: 20)
        await provider.resolveNext(current)
        await fulfillment(of: [delivered], timeout: 1)
        XCTAssertEqual(received, [current])
        tracker.stop()
    }
}
