import XCTest
@testable import JiSpr

final class SetupModelsTests: XCTestCase {
    func testUnknownDownloadSizeRemainsIndeterminateWithRealReceivedBytes() {
        let value = SetupTestFixtures.progress(bytes: 42_000, total: nil)
        XCTAssertNil(value.fractionCompleted)
        XCTAssertTrue(value.byteLabel.contains("received"))
        XCTAssertFalse(value.byteLabel.contains("%"))
    }

    func testKnownByteTotalHasBoundedProgressAndZeroTotalIsIndeterminate() {
        XCTAssertEqual(SetupTestFixtures.progress(bytes: 25, total: 100).fractionCompleted, 0.25)
        XCTAssertEqual(SetupTestFixtures.progress(bytes: 200, total: 100).fractionCompleted, 1)
        XCTAssertNil(SetupTestFixtures.progress(bytes: 0, total: 0).fractionCompleted)
    }

    func testInventoryDoesNotTrustAnInstalledModelWithoutVerifiedField() throws {
        let data = Data("""
        {"id":"small","name":"Small","description":"Local","backend":"mlx-whisper",
        "repo_id":"local/small","languages":["en"],"size_hint":"500 MB",
        "available":true,"installed":true}
        """.utf8)
        let model = try JSONDecoder().decode(SetupSpeechModel.self, from: data)
        XCTAssertTrue(model.installed)
        XCTAssertFalse(model.verified)
        XCTAssertFalse(model.isReady)
    }

    func testDownloadProtocolDecodesTypedFields() throws {
        let value = SetupTestFixtures.progress(job: "download-42", bytes: 1024, total: nil)
        let data = try JSONEncoder().encode(value)
        let decoded = try JSONDecoder().decode(DownloadProgress.self, from: data)
        XCTAssertEqual(decoded, value)
        XCTAssertEqual(decoded.jobID, "download-42")
        XCTAssertEqual(decoded.state, .downloading)
    }
}
