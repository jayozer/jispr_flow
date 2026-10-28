import Foundation
import Observation

@MainActor
@Observable
final class SetupStore {
    private(set) var phase: SetupPhase = .welcome
    private(set) var catalog: SetupCatalog?
    private(set) var selectedModelID: String?
    private(set) var language = "en"
    private(set) var polishBackend = "rules"
    private(set) var download: DownloadProgress?
    private(set) var errorMessage: String?
    private(set) var languageNotice: String?
    private(set) var isOnline = false
    private(set) var accessibilityGranted = false
    private(set) var inputMonitoringGranted = false
    private(set) var microphoneGranted = false
    private(set) var engineState: DictationState = .offline
    private(set) var engineDetail = ""
    private(set) var audioLevel = 0.0
    private(set) var hotkey = "fn"
    private(set) var captureMode = "push-to-talk"
    private(set) var practiceText = ""
    private(set) var practiceStarted = false
    private(set) var practiceStartPending = false
    private(set) var practiceSawRecording = false

    @ObservationIgnored var onRequestPermission: ((SetupPermission) -> Void)?
    @ObservationIgnored var onOpenPermissionSettings: ((SetupPermission) -> Void)?
    @ObservationIgnored var onStartPractice: (() -> Void)?
    @ObservationIgnored var onComplete: (() -> Void)?
    @ObservationIgnored var onLater: (() -> Void)?
    @ObservationIgnored private let sendCommand: (String, [String: Any]) -> String

    private var session = 0
    private var catalogRevision = 0
    private var initializedSelection = false
    private var editedFields: Set<String> = []
    private var pending: [String: Request] = [:]
    private var retiredRequests: Set<String> = []
    private var retiredJobs: Set<String> = []
    private var startingModelID: String?
    private var cancelWhenStarted = false
    private var cancellationPreviousState: DownloadState?
    private var appliedSelection: Selection?
    private var practiceRecordID: String?
    private var practiceRecordText: String?
    private var practiceBaseline = ""

    private struct Selection: Equatable {
        let modelID: String?
        let language: String
        let polish: String
    }
    private enum Action {
        case catalog(Int)
        case download(String)
        case cancel(String)
        case apply(Selection)
    }
    private struct Request { let session: Int; let action: Action }

    init(sendCommand: @escaping (String, [String: Any]) -> String) {
        self.sendCommand = sendCommand
    }

    var selectedModel: SetupSpeechModel? { catalog?.models.first { $0.id == selectedModelID } }
    var hotkeyTitle: String { hotkey.lowercased() == "fn" ? "Fn" : hotkey.uppercased() }
    var isLoadingCatalog: Bool {
        pending.values.contains {
            if case let .catalog(revision) = $0.action { return revision == catalogRevision }
            return false
        }
    }
    var isApplying: Bool {
        pending.values.contains { if case .apply = $0.action { true } else { false } }
    }
    var isStartingDownload: Bool { startingModelID != nil }
    var hasActiveDownload: Bool { isStartingDownload || download?.state.isActive == true }
    var allPermissionsGranted: Bool {
        accessibilityGranted && inputMonitoringGranted && microphoneGranted
    }
    var canApplySelection: Bool {
        isOnline && catalog?.canApply == true && selectedModel?.isReady == true
            && selectedModel?.languages.contains(language) == true && !isApplying
            && !hasActiveDownload
    }
    var canDownloadSelection: Bool {
        isOnline && selectedModel?.available == true && selectedModel?.isReady == false
            && !hasActiveDownload && !isApplying
    }
    var hasAppliedSelection: Bool { appliedSelection == currentSelection }
    var canFinish: Bool {
        guard isOnline, phase == .practice, hasAppliedSelection, allPermissionsGranted,
              practiceStarted, !practiceStartPending, practiceSawRecording, practiceRecordID != nil,
              let record = practiceRecordText, !record.isEmpty else { return false }
        let actual = practiceText.trimmingCharacters(in: .whitespacesAndNewlines)
        let baseline = practiceBaseline.trimmingCharacters(in: .whitespacesAndNewlines)
        guard actual != baseline else { return false }
        if baseline.isEmpty { return actual == record }
        guard actual.hasPrefix(baseline) else { return false }
        return String(actual.dropFirst(baseline.count))
            .trimmingCharacters(in: .whitespacesAndNewlines) == record
    }
    var availableLanguages: [String] {
        let codes = phase == .models && selectedModel != nil
            ? selectedModel!.languages
            : catalog?.models.flatMap(\.languages) ?? ["en", "auto"]
        let all = Set(codes + [language])
        let common = ["auto", "en", "es", "fr", "de", "it", "pt", "ja", "zh", "hi"]
        return common.filter(all.contains) + all.subtracting(common).sorted()
    }

    func beginSession() {
        guard !isOnline else { refreshCatalog(); return }
        session += 1
        isOnline = true
        errorMessage = nil
        refreshCatalog()
    }

    func endSession() {
        session += 1
        retiredRequests.formUnion(pending.keys)
        pending.removeAll()
        if let download { retiredJobs.insert(download.jobID) }
        download = nil
        startingModelID = nil
        cancelWhenStarted = false
        appliedSelection = nil
        isOnline = false
        resetPractice()
        engineState = .offline
        audioLevel = 0
        phase = .welcome
        errorMessage = "The local engine stopped. Restart it to continue setup."
    }

    func openGuide() {
        if phase == .complete { phase = .welcome }
        refreshCatalog()
    }

    func openModels() {
        resetPractice()
        phase = .models
        refreshCatalog()
    }

    func refreshCatalog() {
        guard isOnline else { return }
        catalogRevision += 1
        send("setup_catalog", action: .catalog(catalogRevision))
    }

    func selectModel(_ id: String) {
        guard !isApplying, catalog?.models.contains(where: { $0.id == id }) == true else { return }
        selectedModelID = id
        editedFields.insert("model")
        normalizeLanguage()
        resetPractice()
    }

    func setLanguage(_ value: String) {
        guard !isApplying, availableLanguages.contains(value) else { return }
        language = value
        editedFields.insert("language")
        languageNotice = nil
        resetPractice()
    }

    func setPolishBackend(_ value: String) {
        guard !isApplying, value == "rules" || value == "lmstudio" else { return }
        polishBackend = value
        editedFields.insert("polish")
        resetPractice()
    }

    func continueFromWelcome() {
        guard isOnline, catalog != nil else { return }
        if selectedModel?.available != true || selectedModel?.languages.contains(language) != true {
            let compatible = catalog?.models.filter { $0.available && $0.languages.contains(language) } ?? []
            guard let chosen = compatible.first(where: \.installed) ?? compatible.first else {
                errorMessage = "No available model supports \(Self.languageTitle(language)). Choose another language to continue."
                return
            }
            selectedModelID = chosen.id
            editedFields.insert("model")
        }
        errorMessage = nil
        languageNotice = nil
        phase = .models
    }

    func back() {
        guard !isApplying else { return }
        resetPractice()
        switch phase {
        case .models: phase = .welcome
        case .permissions: phase = .models
        case .practice: phase = .permissions
        case .welcome, .complete: break
        }
    }

    func applySelection() {
        guard canApplySelection, let selectedModelID else { return }
        errorMessage = nil
        send("setup_apply", payload: [
            "model_id": selectedModelID, "language": language, "polish_backend": polishBackend,
        ], action: .apply(currentSelection))
    }

    func downloadSelectedModel() {
        guard canDownloadSelection, let selectedModelID else { return }
        if let download { retiredJobs.insert(download.jobID) }
        download = nil
        startingModelID = selectedModelID
        cancelWhenStarted = false
        errorMessage = nil
        send("model_download", payload: ["model_id": selectedModelID], action: .download(selectedModelID))
    }

    func cancelActiveDownload() {
        guard isOnline else { return }
        if startingModelID != nil, download == nil { cancelWhenStarted = true; return }
        guard let current = download, current.state.isActive, current.state != .cancelling,
              !pending.values.contains(where: {
                  if case let .cancel(jobID) = $0.action { return jobID == current.jobID }
                  return false
              }) else { return }
        var cancelling = current
        cancellationPreviousState = current.state
        cancelling.state = .cancelling
        download = cancelling
        send("model_cancel", payload: ["job_id": current.jobID], action: .cancel(current.jobID))
    }

    func receiveDownload(_ progress: DownloadProgress) {
        acceptDownload(progress, allowExisting: false)
    }

    func updatePermissions(accessibility: Bool, inputMonitoring: Bool, microphone: Bool) {
        accessibilityGranted = accessibility
        inputMonitoringGranted = inputMonitoring
        microphoneGranted = microphone
    }

    func isGranted(_ permission: SetupPermission) -> Bool {
        switch permission {
        case .accessibility: accessibilityGranted
        case .inputMonitoring: inputMonitoringGranted
        case .microphone: microphoneGranted
        }
    }

    func requestPermission(_ permission: SetupPermission) { onRequestPermission?(permission) }
    func openPermissionSettings(_ permission: SetupPermission) { onOpenPermissionSettings?(permission) }

    func continueToPractice() {
        guard isOnline, hasAppliedSelection, allPermissionsGranted else { return }
        resetPractice()
        phase = .practice
    }

    func startPractice() {
        guard isOnline, phase == .practice, hasAppliedSelection, allPermissionsGranted,
              !practiceStartPending, !engineState.isBusy else { return }
        resetPractice()
        practiceStarted = true
        practiceStartPending = true
        onStartPractice?()
    }

    func updateHotkey(_ value: String) { hotkey = value.isEmpty ? "fn" : value }
    func updateCaptureMode(_ mode: String) {
        captureMode = mode == "hands-free" ? mode : "push-to-talk"
    }

    func updateEngine(state: DictationState, detail: String, audioLevel: Double) {
        if practiceStarted {
            practiceStartPending = state == .offline
        }
        if phase == .practice, practiceStarted, state == .recording, engineState != .recording {
            practiceSawRecording = true
            practiceBaseline = practiceText
            practiceRecordID = nil
            practiceRecordText = nil
        }
        engineState = state
        engineDetail = detail
        self.audioLevel = audioLevel.isFinite ? min(1, max(0, audioLevel)) : 0
    }

    func setPracticeText(_ text: String) { practiceText = text }

    func receivePracticeDictation(id: String, text: String, appID: String? = nil) {
        guard isOnline, phase == .practice, practiceStarted, practiceSawRecording,
              !id.isEmpty, appID?.isEmpty != true else { return }
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        practiceRecordID = id
        practiceRecordText = text
    }

    func finish() {
        guard canFinish else { return }
        phase = .complete
        onComplete?()
    }

    func later() {
        onWindowClosed()
        onLater?()
    }

    func onWindowClosed() {
        resetPractice()
        cancelActiveDownload()
    }

    @discardableResult
    func receiveReply(id: String, result: [String: JSONValue]?) -> Bool {
        if retiredRequests.remove(id) != nil { return true }
        guard let request = pending.removeValue(forKey: id) else { return false }
        guard request.session == session, isOnline else { return true }
        do {
            let data = try JSONEncoder().encode(JSONValue.object(result ?? [:]))
            switch request.action {
            case let .catalog(revision):
                guard revision == catalogRevision else { return true }
                let value = try JSONDecoder().decode(SetupCatalog.self, from: data)
                applyCatalog(value)
            case let .download(modelID):
                let value = try JSONDecoder().decode(DownloadReply.self, from: data)
                guard let progress = value.download, progress.modelID == modelID else {
                    throw SetupReplyError.invalid
                }
                acceptDownload(progress, allowExisting: false)
                if startingModelID == modelID { startingModelID = nil }
            case let .cancel(jobID):
                let value = try JSONDecoder().decode(DownloadReply.self, from: data)
                if let progress = value.download, progress.jobID == jobID {
                    acceptDownload(progress, allowExisting: false)
                } else {
                    restoreCancellation(jobID: jobID)
                    refreshCatalog()
                }
            case let .apply(selection):
                let value = try JSONDecoder().decode(ApplyReply.self, from: data)
                guard value.applied, selection == currentSelection else { return true }
                appliedSelection = selection
                resetPractice()
                phase = .permissions
            }
        } catch {
            if case .download = request.action { startingModelID = nil }
            if case let .cancel(jobID) = request.action { restoreCancellation(jobID: jobID) }
            errorMessage = "JiSpr could not read this setup response. Refresh model status and try again."
        }
        return true
    }

    @discardableResult
    func receiveError(id: String, message: String) -> Bool {
        if retiredRequests.remove(id) != nil { return true }
        guard let request = pending.removeValue(forKey: id) else { return false }
        guard request.session == session, isOnline else { return true }
        if case let .catalog(revision) = request.action, revision != catalogRevision { return true }
        if case .download = request.action { startingModelID = nil }
        if case let .cancel(jobID) = request.action {
            guard download?.jobID == jobID else { return true }
            restoreCancellation(jobID: jobID)
        }
        errorMessage = message
        return true
    }

    private var currentSelection: Selection {
        Selection(modelID: selectedModelID, language: language, polish: polishBackend)
    }

    private func applyCatalog(_ value: SetupCatalog) {
        catalog = value
        if !initializedSelection {
            if !editedFields.contains("model") {
                selectedModelID = value.currentModelID
                    ?? value.models.first(where: { $0.available && $0.isReady })?.id
                    ?? value.models.first(where: \.available)?.id
            }
            if !editedFields.contains("language") { language = value.currentLanguage }
            if !editedFields.contains("polish") { polishBackend = value.polishBackend ?? "rules" }
            initializedSelection = true
            normalizeLanguage()
        }
        if let progress = value.download { acceptDownload(progress, allowExisting: true) }
    }

    private func normalizeLanguage() {
        guard let selectedModel, !selectedModel.languages.contains(language) else { return }
        language = selectedModel.languages.contains("auto") ? "auto"
            : selectedModel.languages.contains("en") ? "en"
            : selectedModel.languages.first ?? "en"
        languageNotice = "\(selectedModel.name) uses \(Self.languageTitle(language))."
    }

    private func acceptDownload(_ progress: DownloadProgress, allowExisting: Bool) {
        guard isOnline, !progress.jobID.isEmpty, !retiredJobs.contains(progress.jobID),
              progress.downloadedBytes >= 0,
              progress.totalBytes == nil || progress.totalBytes! >= 0,
              catalog?.models.contains(where: { $0.id == progress.modelID }) == true else { return }
        if let current = download, current.jobID == progress.jobID {
            guard current.modelID == progress.modelID else { return }
            if current.state.isTerminal, progress.state != current.state { return }
            if current.state == .cancelling,
               progress.state == .downloading || progress.state == .preparing { return }
            if current.state == .downloading, progress.state == .preparing { return }
            if progress.state.isActive, progress.downloadedBytes < current.downloadedBytes { return }
        } else {
            guard startingModelID == progress.modelID || allowExisting else { return }
            if let current = download, current.state.isActive { return }
        }
        let wasCompleted = download?.jobID == progress.jobID && download?.state == .completed
        download = progress
        if progress.state == .error { errorMessage = progress.error ?? "The model could not be prepared. Retry when you're ready." }
        if progress.state == .completed, !wasCompleted { refreshCatalog() }
        if cancelWhenStarted, progress.state.isActive {
            cancelWhenStarted = false
            cancelActiveDownload()
        }
    }

    private func resetPractice() {
        practiceStarted = false
        practiceStartPending = false
        practiceSawRecording = false
        practiceText = ""
        practiceBaseline = ""
        practiceRecordID = nil
        practiceRecordText = nil
    }

    private func restoreCancellation(jobID: String) {
        guard var current = download, current.jobID == jobID, current.state == .cancelling else { return }
        current.state = cancellationPreviousState ?? .downloading
        download = current
        cancellationPreviousState = nil
    }

    private func send(_ command: String, payload: [String: Any] = [:], action: Action) {
        let id = sendCommand(command, payload)
        pending[id] = Request(session: session, action: action)
    }

    static func languageTitle(_ code: String) -> String {
        code == "auto" ? "Automatic language detection"
            : Locale.current.localizedString(forLanguageCode: code)?.capitalized ?? code
    }
    private struct DownloadReply: Decodable { let download: DownloadProgress? }
    private struct ApplyReply: Decodable { let applied: Bool }
    private enum SetupReplyError: Error { case invalid }
}
