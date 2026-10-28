import XCTest
@testable import JiSpr

final class SetupStoreTests: XCTestCase {
    @MainActor
    func testExistingModelLanguageAndPolishStayDraftsUntilExplicitApply() {
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(
            currentID: SetupTestFixtures.multilingualID, language: "es", polish: "lmstudio"
        ))
        XCTAssertEqual(model.selectedModelID, SetupTestFixtures.multilingualID)
        XCTAssertEqual(model.language, "es")
        XCTAssertEqual(model.polishBackend, "lmstudio")
        model.openModels()
        model.selectModel(SetupTestFixtures.englishID)
        model.setPolishBackend("rules")
        XCTAssertFalse(commands.calls.contains { $0.command == "setup_apply" })
        model.applySelection()
        XCTAssertEqual(commands.calls.last?.command, "setup_apply")
        XCTAssertEqual(commands.calls.last?.payload["model_id"] as? String, SetupTestFixtures.englishID)
        XCTAssertEqual(commands.calls.last?.payload["language"] as? String, "en")
        XCTAssertEqual(commands.calls.last?.payload["polish_backend"] as? String, "rules")
        XCTAssertEqual(model.phase, .models)
        model.receiveReply(id: commands.lastID("setup_apply"), result: ["applied": .bool(true)])
        XCTAssertEqual(model.phase, .permissions)
    }

    @MainActor
    func testNewUserDefaultsToRulesWithoutRequiringLMStudio() {
        let (model, _) = makeStore(catalog: SetupTestFixtures.catalog(currentID: nil, polish: nil))
        XCTAssertEqual(model.polishBackend, "rules")
        XCTAssertNotNil(model.selectedModel)
    }

    @MainActor
    func testCatalogRefreshPreservesEditedChoices() {
        let (model, commands) = makeStore()
        model.openModels()
        model.selectModel(SetupTestFixtures.multilingualID)
        model.setLanguage("es")
        model.setPolishBackend("lmstudio")
        model.refreshCatalog()
        model.receiveReply(id: commands.lastID("setup_catalog"), result: SetupTestFixtures.catalog())
        XCTAssertEqual(model.selectedModelID, SetupTestFixtures.multilingualID)
        XCTAssertEqual(model.language, "es")
        XCTAssertEqual(model.polishBackend, "lmstudio")
    }

    @MainActor
    func testOnlyNewestCatalogReplyAndErrorAreAccepted() {
        let commands = SetupTestCommands()
        let model = SetupStore { commands.send($0, payload: $1) }
        model.beginSession()
        let old = commands.lastID("setup_catalog")
        model.refreshCatalog()
        model.receiveReply(id: commands.lastID("setup_catalog"), result: SetupTestFixtures.catalog(
            currentID: SetupTestFixtures.multilingualID, language: "es"
        ))
        model.receiveError(id: old, message: "Old catalog failed")
        XCTAssertNil(model.errorMessage)
        XCTAssertEqual(model.selectedModelID, SetupTestFixtures.multilingualID)
        XCTAssertFalse(model.isLoadingCatalog)
    }

    @MainActor
    func testUnavailableOrUnverifiedModelsCannotBeApplied() {
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(verified: false))
        model.openModels()
        XCTAssertFalse(model.canApplySelection)
        XCTAssertTrue(model.canDownloadSelection)
        model.applySelection()
        XCTAssertFalse(commands.calls.contains { $0.command == "setup_apply" })
    }

    @MainActor
    func testUnsupportedBackendCannotBeDownloadedOrAppliedEvenWithCachedFiles() {
        let value = SetupTestFixtures.model(
            id: SetupTestFixtures.englishID, name: "Unavailable model", languages: ["en"],
            installed: true, verified: true, available: false
        )
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(models: [value]))
        model.openModels()
        model.downloadSelectedModel()
        model.applySelection()
        XCTAssertFalse(model.canDownloadSelection)
        XCTAssertFalse(model.canApplySelection)
        XCTAssertFalse(commands.calls.contains { $0.command == "model_download" || $0.command == "setup_apply" })
    }

    @MainActor
    func testConfigurationLocksRemainVisibleAndDoNotSendApply() {
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(
            canApply: false, notice: "Model settings are controlled by an environment override."
        ))
        model.openModels()
        model.applySelection()
        XCTAssertFalse(model.canApplySelection)
        XCTAssertTrue(model.catalog?.configurationNotice?.contains("environment") == true)
        XCTAssertFalse(commands.calls.contains { $0.command == "setup_apply" })
    }

    @MainActor
    func testEnglishOnlySelectionExplainsLanguageChangeAndFiltersChoices() {
        let (model, _) = makeStore(catalog: SetupTestFixtures.catalog(
            currentID: SetupTestFixtures.multilingualID, language: "es"
        ))
        model.openModels()
        model.selectModel(SetupTestFixtures.englishID)
        XCTAssertEqual(model.language, "en")
        XCTAssertFalse(model.availableLanguages.contains("es"))
        XCTAssertNotNil(model.languageNotice)
        model.setLanguage("es")
        XCTAssertEqual(model.language, "en")
    }

    @MainActor
    func testWelcomeLanguageChoiceSelectsACompatibleModelWithoutChangingTheLanguage() {
        let models = [
            SetupTestFixtures.model(id: SetupTestFixtures.multilingualID, name: "Parakeet v3",
                languages: ["auto", "en", "es"], installed: true, verified: true),
            SetupTestFixtures.model(id: "whisper-turbo-mlx", name: "Whisper Turbo",
                languages: ["auto", "en", "zh"], installed: false, verified: false),
        ]
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(
            currentID: SetupTestFixtures.multilingualID, models: models
        ))
        model.setLanguage("zh")
        model.continueFromWelcome()
        XCTAssertEqual(model.phase, .models)
        XCTAssertEqual(model.language, "zh")
        XCTAssertEqual(model.selectedModelID, "whisper-turbo-mlx")
        XCTAssertFalse(commands.calls.contains { $0.command == "model_download" || $0.command == "setup_apply" })
    }

    @MainActor
    func testDownloadIsExplicitAndCompletedEventRequiresFreshVerifiedInventory() {
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(installed: false, verified: false))
        model.openModels()
        XCTAssertFalse(commands.calls.contains { $0.command == "model_download" })
        model.downloadSelectedModel()
        let request = commands.lastID("model_download")
        let first = SetupTestFixtures.progress(state: .preparing, bytes: 0, total: nil)
        model.receiveDownload(first)
        model.receiveReply(id: request, result: ["download": SetupTestFixtures.progressJSON(first)])
        model.receiveDownload(SetupTestFixtures.progress(state: .completed, bytes: 100, total: 100))
        XCTAssertFalse(model.canApplySelection)
        XCTAssertEqual(commands.calls.last?.command, "setup_catalog")
        model.receiveReply(id: commands.lastID("setup_catalog"), result: SetupTestFixtures.catalog())
        XCTAssertTrue(model.canApplySelection)
    }

    @MainActor
    func testUnknownSizeAndCacheVerificationStayIndeterminateWithoutFakeBytes() {
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(verified: false))
        model.openModels()
        model.downloadSelectedModel()
        let request = commands.lastID("model_download")
        let preparing = SetupTestFixtures.progress(state: .preparing, bytes: 0, total: nil)
        model.receiveReply(id: request, result: ["download": SetupTestFixtures.progressJSON(preparing)])
        XCTAssertNil(model.download?.fractionCompleted)
        XCTAssertEqual(model.download?.downloadedBytes, 0)
        model.receiveDownload(SetupTestFixtures.progress(bytes: 42_000, total: nil))
        XCTAssertNil(model.download?.fractionCompleted)
        XCTAssertEqual(model.download?.downloadedBytes, 42_000)
    }

    @MainActor
    func testCacheVerificationCanFallBackToRealTransferWithoutRegressingCounters() {
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(verified: false))
        model.downloadSelectedModel()
        let preparing = SetupTestFixtures.progress(state: .preparing, bytes: 0, total: nil)
        model.receiveReply(id: commands.lastID("model_download"), result: ["download": SetupTestFixtures.progressJSON(preparing)])
        model.receiveDownload(SetupTestFixtures.progress(bytes: 0, total: 100))
        model.receiveDownload(SetupTestFixtures.progress(bytes: 25, total: 100))
        XCTAssertEqual(model.download?.fractionCompleted, 0.25)
        model.receiveDownload(SetupTestFixtures.progress(bytes: 10, total: 100))
        XCTAssertEqual(model.download?.downloadedBytes, 25)
    }

    @MainActor
    func testWrongJobsAndWrongModelEventsCannotChangeCurrentDownload() {
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(installed: false, verified: false))
        model.downloadSelectedModel()
        let progress = SetupTestFixtures.progress(bytes: 25, total: 100)
        model.receiveReply(id: commands.lastID("model_download"), result: ["download": SetupTestFixtures.progressJSON(progress)])
        model.receiveDownload(SetupTestFixtures.progress(job: "other-job", model: SetupTestFixtures.multilingualID))
        model.receiveDownload(SetupTestFixtures.progress(model: SetupTestFixtures.multilingualID))
        XCTAssertEqual(model.download, progress)
    }

    @MainActor
    func testOldModelCompletionDoesNotMakeTheNewSelectionReady() {
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(installed: false, verified: false))
        model.downloadSelectedModel()
        let preparing = SetupTestFixtures.progress(state: .preparing, bytes: 0, total: nil)
        model.receiveReply(id: commands.lastID("model_download"), result: ["download": SetupTestFixtures.progressJSON(preparing)])
        model.selectModel(SetupTestFixtures.multilingualID)
        model.receiveDownload(SetupTestFixtures.progress(state: .completed))
        XCTAssertEqual(model.selectedModelID, SetupTestFixtures.multilingualID)
        XCTAssertFalse(model.canApplySelection)
    }

    @MainActor
    func testCancelAndRetryRejectLateCompletionFromTheCancelledJob() {
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(installed: false, verified: false))
        model.downloadSelectedModel()
        let active = SetupTestFixtures.progress(bytes: 25, total: 100)
        model.receiveReply(id: commands.lastID("model_download"), result: ["download": SetupTestFixtures.progressJSON(active)])
        model.cancelActiveDownload()
        XCTAssertEqual(model.download?.state, .cancelling)
        let cancelled = SetupTestFixtures.progress(state: .cancelled, bytes: 25, total: 100)
        model.receiveReply(id: commands.lastID("model_cancel"), result: ["download": SetupTestFixtures.progressJSON(cancelled)])
        model.downloadSelectedModel()
        model.receiveDownload(SetupTestFixtures.progress(state: .completed, bytes: 100, total: 100))
        XCTAssertNil(model.download)
        let retry = SetupTestFixtures.progress(job: "job-2", bytes: 1, total: 100)
        model.receiveReply(id: commands.lastID("model_download"), result: ["download": SetupTestFixtures.progressJSON(retry)])
        XCTAssertEqual(model.download?.jobID, "job-2")
        XCTAssertEqual(model.download?.downloadedBytes, 1)
    }

    @MainActor
    func testCancelErrorAllowsAnotherCancellationAttempt() {
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(installed: false, verified: false))
        model.downloadSelectedModel()
        let active = SetupTestFixtures.progress()
        model.receiveReply(id: commands.lastID("model_download"), result: ["download": SetupTestFixtures.progressJSON(active)])
        model.cancelActiveDownload()
        model.receiveError(id: commands.lastID("model_cancel"), message: "Cancellation could not be confirmed.")
        XCTAssertEqual(model.download?.state, .downloading)
        model.cancelActiveDownload()
        XCTAssertEqual(commands.calls.filter { $0.command == "model_cancel" }.count, 2)
    }

    @MainActor
    func testLaterBeforeJobReplyCancelsAsSoonAsTheJobIDArrives() {
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(installed: false, verified: false))
        model.downloadSelectedModel()
        let downloadRequest = commands.lastID("model_download")
        var laterCalled = false
        var completed = false
        model.onLater = { laterCalled = true }
        model.onComplete = { completed = true }
        model.later()
        XCTAssertTrue(laterCalled)
        XCTAssertFalse(completed)
        let progress = SetupTestFixtures.progress(state: .preparing, bytes: 0, total: nil)
        model.receiveReply(id: downloadRequest, result: ["download": SetupTestFixtures.progressJSON(progress)])
        XCTAssertEqual(commands.calls.last?.command, "model_cancel")
        XCTAssertEqual(commands.calls.last?.payload["job_id"] as? String, "job-1")
    }

    @MainActor
    func testAllThreePermissionsAndAnApplyReplyAreRequiredBeforePractice() {
        let (model, commands) = makeStore()
        model.updatePermissions(accessibility: true, inputMonitoring: true, microphone: false)
        model.continueToPractice()
        XCTAssertEqual(model.phase, .welcome)
        model.openModels()
        model.applySelection()
        model.receiveReply(id: commands.lastID("setup_apply"), result: ["applied": .bool(true)])
        model.continueToPractice()
        XCTAssertEqual(model.phase, .permissions)
        model.updatePermissions(accessibility: true, inputMonitoring: true, microphone: true)
        model.continueToPractice()
        XCTAssertEqual(model.phase, .practice)
    }

    @MainActor
    func testNoiseTypingAndAnUnarmedDictationNeverFinishPractice() {
        let (model, commands) = makeStore()
        enterPractice(model, commands: commands)
        model.setPracticeText("Hello JiSpr")
        model.receivePracticeDictation(id: "unarmed", text: "Hello JiSpr")
        XCTAssertFalse(model.canFinish)
        model.startPractice()
        model.updateEngine(state: .idle, detail: "Ready", audioLevel: 0.9)
        model.setPracticeText("Hello JiSpr")
        XCTAssertFalse(model.canFinish)
        model.updateEngine(state: .recording, detail: "Listening", audioLevel: 1)
        model.updateEngine(state: .idle, detail: "No speech", audioLevel: 0)
        XCTAssertFalse(model.canFinish)
    }

    @MainActor
    func testActualRecordingRecordAndMatchingChangedEditorTextAreAllRequired() {
        let (model, commands) = makeStore()
        enterPractice(model, commands: commands)
        model.startPractice()
        model.updateEngine(state: .recording, detail: "Listening", audioLevel: 0.6)
        model.receivePracticeDictation(id: "record-1", text: "Hello JiSpr", appID: "com.example.test")
        XCTAssertFalse(model.canFinish)
        model.setPracticeText("Different words")
        XCTAssertFalse(model.canFinish)
        model.setPracticeText("Hello JiSpr\n")
        XCTAssertTrue(model.canFinish)
        var completed = false
        model.onComplete = { completed = true }
        model.finish()
        XCTAssertTrue(completed)
        XCTAssertEqual(model.phase, .complete)
    }

    @MainActor
    func testEditorCanUpdateBeforeTheRealDictationEvent() {
        let (model, commands) = makeStore()
        enterPractice(model, commands: commands)
        model.startPractice()
        model.updateEngine(state: .recording, detail: "Listening", audioLevel: 0.5)
        model.setPracticeText("Words arrived")
        XCTAssertFalse(model.canFinish)
        model.receivePracticeDictation(id: "record", text: "Words arrived")
        XCTAssertTrue(model.canFinish)
        model.setPracticeText("User edited the result")
        XCTAssertFalse(model.canFinish)
    }

    @MainActor
    func testStartingEngineCannotLookReadyAndEngineErrorCanBeRetried() {
        let (model, commands) = makeStore()
        enterPractice(model, commands: commands)
        model.startPractice()
        model.updateEngine(state: .offline, detail: "Loading your selected speech model…", audioLevel: 0)
        XCTAssertTrue(model.practiceStartPending)
        var repeatedStarts = 0
        model.onStartPractice = { repeatedStarts += 1 }
        model.startPractice()
        XCTAssertEqual(repeatedStarts, 0)
        XCTAssertFalse(model.canFinish)
        model.updateEngine(state: .error, detail: "Model load failed", audioLevel: 0)
        XCTAssertFalse(model.practiceStartPending)
        model.startPractice()
        XCTAssertTrue(model.practiceStartPending)
        model.updateEngine(state: .idle, detail: "Ready", audioLevel: 0)
        XCTAssertFalse(model.practiceStartPending)
        XCTAssertFalse(model.canFinish)
    }

    @MainActor
    func testWindowCloseCancelsDownloadAndClearsPracticeEvidence() {
        let (model, commands) = makeStore()
        enterPractice(model, commands: commands)
        model.startPractice()
        model.updateEngine(state: .recording, detail: "Listening", audioLevel: 0.4)
        model.setPracticeText("Real words")
        model.receivePracticeDictation(id: "record", text: "Real words")
        XCTAssertTrue(model.canFinish)
        model.onWindowClosed()
        XCTAssertFalse(model.practiceStarted)
        XCTAssertEqual(model.practiceText, "")
        XCTAssertFalse(model.canFinish)
    }

    @MainActor
    func testHostExitRejectsLateRepliesAndDownloadEvents() {
        let (model, commands) = makeStore(catalog: SetupTestFixtures.catalog(installed: false, verified: false))
        model.downloadSelectedModel()
        let request = commands.lastID("model_download")
        model.endSession()
        let late = SetupTestFixtures.progress()
        XCTAssertTrue(model.receiveReply(id: request, result: ["download": SetupTestFixtures.progressJSON(late)]))
        model.receiveDownload(late)
        XCTAssertNil(model.download)
        XCTAssertFalse(model.isOnline)
        XCTAssertFalse(model.canApplySelection)
        XCTAssertFalse(model.canFinish)
    }

    @MainActor
    private func makeStore(
        catalog: [String: JSONValue] = SetupTestFixtures.catalog()
    ) -> (SetupStore, SetupTestCommands) {
        let commands = SetupTestCommands()
        let model = SetupStore { commands.send($0, payload: $1) }
        model.beginSession()
        model.receiveReply(id: commands.lastID("setup_catalog"), result: catalog)
        return (model, commands)
    }

    @MainActor
    private func enterPractice(_ model: SetupStore, commands: SetupTestCommands) {
        model.openModels()
        model.applySelection()
        model.receiveReply(id: commands.lastID("setup_apply"), result: ["applied": .bool(true)])
        model.updatePermissions(accessibility: true, inputMonitoring: true, microphone: true)
        model.continueToPractice()
    }
}
