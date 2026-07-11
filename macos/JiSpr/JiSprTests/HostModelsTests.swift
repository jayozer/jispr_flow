import XCTest
@testable import JiSpr

final class HostModelsTests: XCTestCase {
    func testDecodesSnapshotWithTypedSettings() throws {
        let data = Data(
            """
            {
              "v": 1,
              "event": "snapshot",
              "snapshot": {
                "config_path": "/tmp/config.toml",
                "data_dir": "/tmp/data",
                "settings": {
                  "mode": {"value": "push-to-talk", "source": "toml", "editable": true},
                  "floating_pill": {"value": true, "source": "environment", "editable": false},
                  "history_max_entries": {"value": 5000, "source": "default", "editable": true}
                },
                "options": {"mode": ["push-to-talk", "hands-free"]},
                "presets": {},
                "styles": ["default"],
                "transforms": ["Polish"],
                "dictionary": [{"term": "JiSpr", "starred": true, "uses": 2}],
                "aliases": {"jisper": "JiSpr"}
              }
            }
            """.utf8
        )

        let message = try JSONDecoder().decode(HostMessage.self, from: data)

        XCTAssertEqual(message.snapshot?.settings["mode"]?.value, .string("push-to-talk"))
        XCTAssertEqual(message.snapshot?.settings["floating_pill"]?.value, .bool(true))
        XCTAssertEqual(message.snapshot?.settings["history_max_entries"]?.value, .int(5000))
        XCTAssertEqual(message.snapshot?.dictionary.first?.term, "JiSpr")
    }

    func testDictationStateMapsMenuSymbolsAndBusyStates() {
        XCTAssertEqual(DictationState(hostValue: "recording").symbol, "waveform.circle.fill")
        XCTAssertEqual(
            DictationState.warning.symbol,
            "waveform.badge.exclamationmark"
        )
        XCTAssertTrue(DictationState.processing.isBusy)
        XCTAssertFalse(DictationState.idle.isBusy)
        XCTAssertEqual(DictationState(hostValue: "unexpected"), .error)
    }

    func testJSONValueBridgesToFoundationTypes() {
        let value = JSONValue.object([
            "enabled": .bool(true),
            "count": .int(3),
            "names": .array([.string("JiSpr")]),
        ])

        let object = value.foundationValue as? [String: Any]

        XCTAssertEqual(object?["enabled"] as? Bool, true)
        XCTAssertEqual(object?["count"] as? Int, 3)
        XCTAssertEqual(object?["names"] as? [String], ["JiSpr"])
    }
}
