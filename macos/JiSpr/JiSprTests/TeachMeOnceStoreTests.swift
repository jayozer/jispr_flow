import XCTest
@testable import JiSpr

final class TeachMeOnceStoreTests: XCTestCase {
    @MainActor
    func testFirstOpenLoadsTheMostRecentOfSeveralDictationEvents() {
        let (model, _) = makeStore()
        model.receiveDictation(record())
        model.receiveDictation(record(id: "latest", text: "Most recent dictation"))
        model.openReview()
        XCTAssertEqual(model.reviewedDictation?.id, "latest")
        XCTAssertEqual(model.draft, "Most recent dictation")
    }

    @MainActor
    func testRecordThenNullBeforeFirstOpenKeepsTheZeroState() {
        let (model, _) = makeStore()
        model.receiveDictation(record())
        model.receiveDictation(nil)
        model.openReview()
        XCTAssertNil(model.latestDictation)
        XCTAssertNil(model.reviewedDictation)
        XCTAssertEqual(model.draft, "")
        XCTAssertFalse(model.hasNewerDictation)
    }

    @MainActor
    func testNullEventClearsAnUntouchedReviewInsteadOfShowingItAsLatest() {
        let (model, _) = makeStore()
        model.receiveDictation(record())
        model.openReview()
        model.receiveDictation(nil)
        XCTAssertNil(model.latestDictation)
        XCTAssertNil(model.reviewedDictation)
        XCTAssertEqual(model.draft, "")
    }

    @MainActor
    func testNullEventAndReopeningPreserveAnActivelyEditedReview() {
        let (model, _) = makeStore()
        model.receiveDictation(record())
        model.openReview()
        model.setDraft("Hello JiSpr")
        model.receiveDictation(nil)
        model.openReview()
        XCTAssertNil(model.latestDictation)
        XCTAssertEqual(model.reviewedDictation?.id, "first")
        XCTAssertEqual(model.draft, "Hello JiSpr")
        XCTAssertTrue(model.hasEditedDraft)
    }

    @MainActor
    func testReopeningAdoptsTheLatestRecordOnlyWhenTheReviewIsUnedited() {
        let (model, _) = makeStore()
        model.receiveDictation(record())
        model.openReview()
        model.receiveDictation(record(id: "next", text: "A newer thought"))
        XCTAssertEqual(model.reviewedDictation?.id, "first")
        model.openReview()
        XCTAssertEqual(model.reviewedDictation?.id, "next")
        XCTAssertEqual(model.draft, "A newer thought")
        model.setDraft("A corrected newer thought")
        model.receiveDictation(record(id: "last", text: "The latest thought"))
        model.openReview()
        XCTAssertEqual(model.reviewedDictation?.id, "next")
        XCTAssertEqual(model.draft, "A corrected newer thought")
    }

    @MainActor
    func testVerbatimNoticeTracksConfigurationWithoutChangingTheDraft() {
        let (model, _) = makeStore()
        XCTAssertFalse(model.isVerbatim)
        model.receiveDictation(record())
        model.openReview()
        model.setDraft("Hello JiSpr")
        model.setVerbatim(true)
        XCTAssertTrue(model.isVerbatim)
        XCTAssertEqual(model.draft, "Hello JiSpr")
        model.setVerbatim(false)
        XCTAssertFalse(model.isVerbatim)
    }

    @MainActor
    func testOpeningReviewAdoptsLastDictationAndDefaultsToItsActualApp() {
        let (model, commands) = makeStore()
        model.receiveDictation(record())
        model.openReview()
        XCTAssertEqual(model.reviewedDictation?.id, "first")
        XCTAssertEqual(model.draft, "Hello jisper")
        XCTAssertEqual(model.scope, .app)
        XCTAssertEqual(commands.calls.last?.command, "review_latest")
        XCTAssertFalse(commands.calls.contains { $0.command == "correction_learn" })
    }

    @MainActor
    func testUnknownOriginDefaultsToEverywhereAndCannotSelectAppScope() {
        let (model, _) = makeStore()
        model.receiveDictation(record(appID: ""))
        model.openReview()
        XCTAssertEqual(model.scope, .global)
        model.setScope(.app)
        XCTAssertEqual(model.scope, .global)
    }

    @MainActor
    func testNewDictationDoesNotOverwriteEditsAndLoadLatestIsExplicit() {
        let (model, _) = makeStore()
        model.receiveDictation(record())
        model.openReview()
        model.setDraft("Hello JiSpr")
        let next = record(id: "next", text: "A new thought", appID: "com.apple.mail")
        model.receiveDictation(next)
        XCTAssertEqual(model.reviewedDictation?.id, "first")
        XCTAssertEqual(model.draft, "Hello JiSpr")
        XCTAssertTrue(model.hasNewerDictation)
        model.loadLatest()
        XCTAssertEqual(model.reviewedDictation, next)
        XCTAssertEqual(model.draft, "A new thought")
        XCTAssertFalse(model.hasNewerDictation)
    }

    @MainActor
    func testSnapshotsUpdateRulesWithoutReplacingTheReviewDraft() {
        let (model, _) = makeStore()
        model.receiveDictation(record())
        model.openReview()
        model.setDraft("Hello JiSpr")
        model.updateRules([rule()])
        XCTAssertEqual(model.rules, [rule()])
        XCTAssertEqual(model.draft, "Hello JiSpr")
    }

    @MainActor
    func testReviewAsksOnlyForSuggestionsAndRememberSendsTheApprovedPairAndScope() {
        let (model, commands) = makeStore()
        preparePreview(model, commands: commands)
        XCTAssertEqual(model.suggestions, [suggestion])
        XCTAssertEqual(commands.calls.last?.command, "correction_preview")
        XCTAssertEqual(commands.calls.last?.payload["corrected_text"] as? String, "Hello JiSpr")
        XCTAssertFalse(commands.calls.contains { $0.command == "correction_learn" })
        model.remember(suggestion)
        let command = commands.calls.last!
        XCTAssertEqual(command.command, "correction_learn")
        XCTAssertEqual(command.payload["dictation_id"] as? String, "first")
        XCTAssertEqual(command.payload["source"] as? String, "jisper")
        XCTAssertEqual(command.payload["replacement"] as? String, "JiSpr")
        XCTAssertEqual(command.payload["scope"] as? String, "app")
        XCTAssertNil(command.payload["app_id"])
        XCTAssertTrue(model.isMutating)
    }

    @MainActor
    func testGlobalScopeIsExplicitAndUnknownSuggestionsCannotBeRemembered() {
        let (model, commands) = makeStore()
        preparePreview(model, commands: commands)
        let count = commands.calls.count
        model.remember(CorrectionSuggestion(source: "other", replacement: "anything"))
        XCTAssertEqual(commands.calls.count, count)
        model.setScope(.global)
        model.remember(suggestion)
        XCTAssertEqual(commands.calls.last?.payload["scope"] as? String, "global")
    }

    @MainActor
    func testLearnReplyOffersUndoAndNeverReplacesTheDraft() {
        let (model, commands) = makeStore()
        preparePreview(model, commands: commands)
        model.remember(suggestion)
        model.receiveReply(id: commands.calls.last!.id, result: [
            "changed": .bool(true), "undo_token": .string("undo-learn"),
        ])
        XCTAssertEqual(model.undoToken, "undo-learn")
        XCTAssertEqual(model.undoTitle, "Undo last learn")
        XCTAssertEqual(model.draft, "Hello JiSpr")
        XCTAssertEqual(model.suggestions, [])
        model.undo()
        XCTAssertEqual(commands.calls.last?.command, "correction_undo")
        XCTAssertEqual(commands.calls.last?.payload["token"] as? String, "undo-learn")
        model.receiveReply(id: commands.calls.last!.id, result: ["changed": .bool(true)])
        XCTAssertNil(model.undoToken)
        XCTAssertEqual(model.draft, "Hello JiSpr")
    }

    @MainActor
    func testForgetWaitsForTheAuthoritativeSnapshotAndSupportsUndo() {
        let (model, commands) = makeStore()
        model.updateRules([rule()])
        model.forget(rule())
        XCTAssertEqual(commands.calls.last?.command, "correction_forget")
        XCTAssertEqual(commands.calls.last?.payload["rule_id"] as? String, "rule-1")
        XCTAssertEqual(model.rules, [rule()])
        model.updateRules([])
        model.receiveReply(id: commands.calls.last!.id, result: [
            "changed": .bool(true), "undo_token": .string("undo-forget"),
        ])
        XCTAssertEqual(model.rules, [])
        XCTAssertEqual(model.undoToken, "undo-forget")
        XCTAssertEqual(model.undoTitle, "Undo last forget")
    }

    @MainActor
    func testEditingInvalidatesSuggestionsAndLatePreviewResponses() {
        let (model, commands) = makeStore()
        model.receiveDictation(record())
        model.openReview()
        model.setDraft("Hello JiSpr")
        model.reviewChanges()
        let oldRequest = commands.calls.last!.id
        model.setDraft("Hello JiSpr Flow")
        XCTAssertFalse(model.isPreviewing)
        model.receiveReply(id: oldRequest, result: previewResult())
        XCTAssertEqual(model.suggestions, [])
        XCTAssertEqual(model.draft, "Hello JiSpr Flow")
        XCTAssertNil(model.errorMessage)
    }

    @MainActor
    func testLatePreviewErrorAfterLoadingAnotherDictationIsConsumedSilently() {
        let (model, commands) = makeStore()
        model.receiveDictation(record())
        model.openReview()
        model.setDraft("Hello JiSpr")
        model.reviewChanges()
        let oldRequest = commands.calls.last!.id
        model.receiveDictation(record(id: "next", text: "New dictation"))
        model.loadLatest()
        XCTAssertTrue(model.receiveError(id: oldRequest, message: "Old request failed"))
        XCTAssertNil(model.errorMessage)
        XCTAssertEqual(model.draft, "New dictation")
    }

    @MainActor
    func testLatestReplyCannotReplaceANewerDictationEvent() {
        let (model, commands) = makeStore()
        model.openReview()
        let latestRequest = commands.calls.last!.id
        let newer = record(id: "newest", text: "Newest thought")
        model.receiveDictation(newer)
        model.receiveReply(id: latestRequest, result: ["dictation": .null])
        XCTAssertEqual(model.latestDictation, newer)
    }

    @MainActor
    func testPreviewResponseForAnotherDictationIsIgnored() {
        let (model, commands) = makeStore()
        model.receiveDictation(record())
        model.openReview()
        model.setDraft("Hello JiSpr")
        model.reviewChanges()
        model.receiveReply(id: commands.calls.last!.id, result: previewResult(id: "different"))
        XCTAssertEqual(model.suggestions, [])
        XCTAssertEqual(model.draft, "Hello JiSpr")
    }

    @MainActor
    func testPreviewErrorsPreserveEditsAndCanBeRetried() {
        let (model, commands) = makeStore()
        model.receiveDictation(record())
        model.openReview()
        model.setDraft("Hello JiSpr")
        model.reviewChanges()
        model.receiveError(id: commands.calls.last!.id, message: "Original dictation expired.")
        XCTAssertEqual(model.errorMessage, "Original dictation expired.")
        XCTAssertEqual(model.draft, "Hello JiSpr")
        XCTAssertTrue(model.canReviewChanges)
    }

    @MainActor
    func testMalformedReplyPreservesEditsAndReportsTheFailure() {
        let (model, commands) = makeStore()
        model.receiveDictation(record())
        model.openReview()
        model.setDraft("Hello JiSpr")
        model.reviewChanges()
        model.receiveReply(id: commands.calls.last!.id, result: ["unexpected": .bool(true)])
        XCTAssertNotNil(model.errorMessage)
        XCTAssertEqual(model.draft, "Hello JiSpr")
    }

    @MainActor
    func testEngineTerminationClearsTransientDataAndConsumesLateReplies() {
        let (model, commands) = makeStore()
        preparePreview(model, commands: commands)
        model.remember(suggestion)
        let pendingLearn = commands.calls.last!.id
        model.endSession()
        XCTAssertNil(model.latestDictation)
        XCTAssertNil(model.reviewedDictation)
        XCTAssertEqual(model.draft, "")
        XCTAssertEqual(model.suggestions, [])
        XCTAssertFalse(model.isOnline)
        XCTAssertTrue(model.receiveReply(id: pendingLearn, result: [
            "changed": .bool(true), "undo_token": .string("stale-undo"),
        ]))
        XCTAssertNil(model.undoToken)
        model.receiveDictation(record(id: "late"))
        XCTAssertNil(model.latestDictation)
    }

    @MainActor
    func testEmptyLatestReplyKeepsTheZeroStateAndOfflineActionsSendNothing() {
        let (model, commands) = makeStore()
        model.openReview()
        model.receiveReply(id: commands.calls.last!.id, result: ["dictation": .null])
        XCTAssertNil(model.reviewedDictation)
        XCTAssertEqual(model.draft, "")
        model.endSession()
        let count = commands.calls.count
        model.openReview()
        model.reviewChanges()
        model.remember(suggestion)
        model.undo()
        XCTAssertEqual(commands.calls.count, count)
    }

    @MainActor
    func testRulesReadErrorIsIndependentOfTheEditableDraft() {
        let (model, _) = makeStore()
        model.receiveDictation(record())
        model.openReview()
        model.setDraft("Hello JiSpr")
        model.updateRules([], error: "Remembered corrections could not be read.")
        XCTAssertEqual(model.rulesError, "Remembered corrections could not be read.")
        XCTAssertEqual(model.draft, "Hello JiSpr")
    }

    private var suggestion: CorrectionSuggestion {
        CorrectionSuggestion(source: "jisper", replacement: "JiSpr")
    }

    private func record(
        id: String = "first", text: String = "Hello jisper", appID: String = "com.apple.TextEdit"
    ) -> DictationRecord {
        DictationRecord(id: id, text: text, appID: appID, appName: "TextEdit", correctionsApplied: 0)
    }

    private func rule() -> CorrectionRule {
        CorrectionRule(
            id: "rule-1", source: "jisper", replacement: "JiSpr",
            appID: "com.apple.TextEdit", createdAt: "2026-10-27T12:00:00Z"
        )
    }

    private func previewResult(id: String = "first") -> [String: JSONValue] {
        ["dictation_id": .string(id), "suggestions": .array([
            .object(["source": .string("jisper"), "replacement": .string("JiSpr")]),
        ])]
    }

    @MainActor
    private func makeStore() -> (TeachMeOnceStore, Commands) {
        let commands = Commands()
        let model = TeachMeOnceStore { commands.send($0, payload: $1) }
        model.setOnline(true)
        return (model, commands)
    }

    @MainActor
    private func preparePreview(_ model: TeachMeOnceStore, commands: Commands) {
        model.receiveDictation(record())
        model.openReview()
        model.setDraft("Hello JiSpr")
        model.reviewChanges()
        model.receiveReply(id: commands.calls.last!.id, result: previewResult())
    }

    @MainActor
    private final class Commands {
        struct Call {
            let id: String
            let command: String
            let payload: [String: Any]
        }
        var calls: [Call] = []
        func send(_ command: String, payload: [String: Any]) -> String {
            let id = "request-\(calls.count + 1)"
            calls.append(Call(id: id, command: command, payload: payload))
            return id
        }
    }
}
