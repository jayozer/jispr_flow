import Foundation
import Observation

/// Review is user initiated and detached from the editor in the original app.
/// Only explicit Remember / Forget / Undo actions mutate the local rules.
@MainActor
@Observable
final class TeachMeOnceStore {
    private(set) var latestDictation: DictationRecord?
    private(set) var reviewedDictation: DictationRecord?
    private(set) var draft = ""
    private(set) var suggestions: [CorrectionSuggestion] = []
    private(set) var rules: [CorrectionRule] = []
    private(set) var rulesError: String?
    private(set) var scope: CorrectionScope = .global
    private(set) var errorMessage: String?
    private(set) var statusMessage: String?
    private(set) var undoToken: String?
    private(set) var undoTitle = "Undo last learn"
    private(set) var isOnline = false
    private(set) var isVerbatim = false

    @ObservationIgnored private let sendCommand: (String, [String: Any]) -> String
    private var revision = 0
    private var latestRevision = 0
    private var pending: [String: Request] = [:]
    private var retiredRequests: Set<String> = []

    private struct Context: Equatable {
        let dictationID: String?
        let revision: Int
    }

    private enum Action {
        case latest(Int)
        case preview
        case learn(CorrectionSuggestion)
        case forget
        case undo

        var isMutation: Bool {
            switch self {
            case .learn, .forget, .undo: true
            case .latest, .preview: false
            }
        }
    }

    private struct Request {
        let context: Context
        let action: Action
    }

    init(sendCommand: @escaping (String, [String: Any]) -> String) {
        self.sendCommand = sendCommand
    }

    var isLoadingLatest: Bool {
        pending.values.contains { request in
            guard request.context == currentContext else { return false }
            if case .latest = request.action { return true }
            return false
        }
    }

    var isPreviewing: Bool {
        pending.values.contains { request in
            guard request.context == currentContext else { return false }
            if case .preview = request.action { return true }
            return false
        }
    }

    var isMutating: Bool { pending.values.contains { $0.action.isMutation } }
    var hasNewerDictation: Bool {
        latestDictation != nil && latestDictation?.id != reviewedDictation?.id
    }
    var hasEditedDraft: Bool {
        reviewedDictation != nil && draft != reviewedDictation?.text
    }
    var canReviewChanges: Bool {
        isOnline && reviewedDictation != nil
            && draft != reviewedDictation?.text
            && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !isPreviewing && !isMutating
    }

    func setOnline(_ online: Bool) { isOnline = online }
    func setVerbatim(_ verbatim: Bool) { isVerbatim = verbatim }

    func openReview() {
        refreshUneditedReview()
        guard isOnline, !isLoadingLatest else { return }
        send("review_latest", action: .latest(latestRevision))
    }

    func receiveDictation(_ record: DictationRecord?) {
        guard isOnline else { return }
        latestRevision += 1
        latestDictation = record
        // An existing review draft always stays frozen until Load latest.
        // A null event means the newest dictation cannot be reviewed; do not
        // present an older, untouched record as though it were still latest.
        if record == nil { refreshUneditedReview() }
    }

    func updateRules(_ rules: [CorrectionRule], error: String? = nil) {
        self.rules = rules
        rulesError = error
    }

    func loadLatest() {
        guard let latestDictation, !isMutating else { return }
        invalidatePreview()
        reviewedDictation = latestDictation
        draft = latestDictation.text
        scope = latestDictation.hasAppScope ? .app : .global
        errorMessage = nil
        statusMessage = nil
    }

    func setDraft(_ text: String) {
        guard text != draft else { return }
        invalidatePreview()
        draft = text
        errorMessage = nil
        statusMessage = nil
    }

    func setScope(_ scope: CorrectionScope) {
        guard scope != .app || reviewedDictation?.hasAppScope == true else { return }
        self.scope = scope
    }

    func reviewChanges() {
        guard canReviewChanges, let record = reviewedDictation else { return }
        invalidatePreview()
        errorMessage = nil
        statusMessage = nil
        send("correction_preview", payload: [
            "dictation_id": record.id, "corrected_text": draft,
        ], action: .preview)
    }

    func remember(_ suggestion: CorrectionSuggestion) {
        guard isOnline, !isMutating, !isPreviewing,
              suggestions.contains(suggestion), let record = reviewedDictation else { return }
        errorMessage = nil
        statusMessage = nil
        send("correction_learn", payload: [
            "dictation_id": record.id,
            "corrected_text": draft,
            "source": suggestion.source,
            "replacement": suggestion.replacement,
            "scope": scope.rawValue,
        ], action: .learn(suggestion))
    }

    func forget(_ rule: CorrectionRule) {
        guard isOnline, !isMutating, rules.contains(rule) else { return }
        errorMessage = nil
        statusMessage = nil
        send("correction_forget", payload: ["rule_id": rule.id], action: .forget)
    }

    func undo() {
        guard isOnline, !isMutating, let undoToken else { return }
        errorMessage = nil
        statusMessage = nil
        send("correction_undo", payload: ["token": undoToken], action: .undo)
    }

    /// Returns true for owned requests, including stale responses discarded here.
    @discardableResult
    func receiveReply(id: String, result: [String: JSONValue]?) -> Bool {
        if retiredRequests.remove(id) != nil { return true }
        guard let request = pending.removeValue(forKey: id) else { return false }
        guard request.context == currentContext else { return true }
        do {
            let data = try JSONEncoder().encode(JSONValue.object(result ?? [:]))
            let decoder = JSONDecoder()
            switch request.action {
            case let .latest(version):
                guard version == latestRevision else { return true }
                let reply = try decoder.decode(LatestReply.self, from: data)
                latestDictation = reply.dictation
                latestRevision += 1
                refreshUneditedReview()
            case .preview:
                let reply = try decoder.decode(PreviewReply.self, from: data)
                guard reply.dictationID == reviewedDictation?.id else { return true }
                suggestions = reply.suggestions
                if suggestions.isEmpty {
                    statusMessage = "No reusable phrase changes found. Try one short correction at a time."
                }
            case let .learn(suggestion):
                let reply = try decoder.decode(MutationReply.self, from: data)
                suggestions.removeAll { $0 == suggestion }
                if reply.changed {
                    undoToken = reply.undoToken
                    undoTitle = "Undo last learn"
                    statusMessage = "Remembered for future dictations."
                } else {
                    statusMessage = "That correction is already remembered."
                }
            case .forget:
                let reply = try decoder.decode(MutationReply.self, from: data)
                if reply.changed {
                    undoToken = reply.undoToken
                    undoTitle = "Undo last forget"
                }
                statusMessage = reply.changed ? "Correction forgotten." : "Correction was already removed."
            case .undo:
                let reply = try decoder.decode(MutationReply.self, from: data)
                undoToken = nil
                statusMessage = reply.changed ? "Last change undone." : "That change is no longer available."
            }
        } catch {
            errorMessage = "JiSpr could not read this response. Your edits are still here."
        }
        return true
    }

    @discardableResult
    func receiveError(id: String, message: String) -> Bool {
        if retiredRequests.remove(id) != nil { return true }
        guard let request = pending.removeValue(forKey: id) else { return false }
        if request.context == currentContext { errorMessage = message }
        return true
    }

    func endSession() {
        revision += 1
        latestRevision += 1
        retiredRequests.formUnion(pending.keys)
        pending.removeAll()
        isOnline = false
        latestDictation = nil
        reviewedDictation = nil
        draft = ""
        suggestions = []
        scope = .global
        undoToken = nil
        errorMessage = nil
        statusMessage = nil
    }

    private var currentContext: Context {
        Context(dictationID: reviewedDictation?.id, revision: revision)
    }

    private func refreshUneditedReview() {
        guard !hasEditedDraft else { return }
        if let latestDictation {
            guard !isMutating else { return }
            if reviewedDictation?.id != latestDictation.id { loadLatest() }
        } else if reviewedDictation != nil {
            invalidatePreview()
            reviewedDictation = nil
            draft = ""
            scope = .global
            errorMessage = nil
            statusMessage = nil
        }
    }

    private func send(
        _ command: String,
        payload: [String: Any] = [:],
        action: Action
    ) {
        let request = Request(context: currentContext, action: action)
        let id = sendCommand(command, payload)
        pending[id] = request
    }

    private func invalidatePreview() {
        revision += 1
        suggestions = []
    }

    private struct LatestReply: Decodable { let dictation: DictationRecord? }
    private struct PreviewReply: Decodable {
        let dictationID: String
        let suggestions: [CorrectionSuggestion]
        enum CodingKeys: String, CodingKey {
            case dictationID = "dictation_id"
            case suggestions
        }
    }
    private struct MutationReply: Decodable {
        let changed: Bool
        let undoToken: String?
        enum CodingKeys: String, CodingKey {
            case changed
            case undoToken = "undo_token"
        }
    }
}
