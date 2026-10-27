import XCTest
@testable import JiSpr

final class CorrectionModelsTests: XCTestCase {
    func testDecodesDictationEventWithOriginAndAppliedCorrectionCount() throws {
        let message = try JSONDecoder().decode(HostMessage.self, from: Data("""
        {"v":1,"event":"dictation","dictation":{
            "id":"dictation-1","text":"Hello JiSpr","app_id":"com.apple.TextEdit",
            "app_name":"TextEdit","corrections_applied":2
        }}
        """.utf8))
        XCTAssertEqual(message.dictation?.id, "dictation-1")
        XCTAssertEqual(message.dictation?.text, "Hello JiSpr")
        XCTAssertEqual(message.dictation?.appID, "com.apple.TextEdit")
        XCTAssertEqual(message.dictation?.appName, "TextEdit")
        XCTAssertEqual(message.dictation?.correctionsApplied, 2)
    }

    func testLegacySnapshotsDefaultToNoRememberedCorrections() throws {
        let snapshot = try decodeSnapshot(extra: "")
        XCTAssertEqual(snapshot.corrections, [])
        XCTAssertNil(snapshot.correctionsError)
    }

    func testSnapshotDecodesRememberedRulesAndNonblockingReadError() throws {
        let snapshot = try decodeSnapshot(extra: """
        ,"corrections":[{"id":"rule-1","source":"jisper","replacement":"JiSpr",
        "app_id":null,"created_at":"2026-10-27T12:00:00Z"}],
        "corrections_error":"Could not read remembered corrections."
        """)
        XCTAssertEqual(snapshot.corrections.first?.source, "jisper")
        XCTAssertEqual(snapshot.corrections.first?.replacement, "JiSpr")
        XCTAssertEqual(snapshot.corrections.first?.scopeLabel, "Everywhere")
        XCTAssertEqual(snapshot.correctionsError, "Could not read remembered corrections.")
    }

    func testUnknownOriginHasNoAppScopeAndDoesNotProduceAnEmptyAppLabel() {
        let record = DictationRecord(
            id: "1", text: "Hello", appID: "", appName: "", correctionsApplied: 0
        )
        XCTAssertFalse(record.hasAppScope)
        XCTAssertEqual(record.appDisplayName, "Unknown app")
    }

    func testNullDictationEventDecodesWithoutRevivingAnOlderRecord() throws {
        let message = try JSONDecoder().decode(HostMessage.self, from: Data(
            #"{"v":1,"event":"dictation","dictation":null}"#.utf8
        ))
        XCTAssertEqual(message.event, "dictation")
        XCTAssertNil(message.dictation)
    }

    private func decodeSnapshot(extra: String) throws -> HostSnapshot {
        try JSONDecoder().decode(HostSnapshot.self, from: Data("""
        {"data_dir":"/tmp/data","settings":{},"options":{},"presets":{},
        "styles":[],"transforms":[],"dictionary":[],"aliases":{}\(extra)}
        """.utf8))
    }
}
