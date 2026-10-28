import Foundation

struct DictationRecord: Codable, Equatable, Identifiable, Sendable {
    let id: String
    let text: String
    let appID: String?
    let appName: String?
    let correctionsApplied: Int

    var appDisplayName: String {
        if let appName, !appName.isEmpty { return appName }
        if let appID, !appID.isEmpty { return appID }
        return "Unknown app"
    }
    var hasAppScope: Bool { appID?.isEmpty == false }

    enum CodingKeys: String, CodingKey {
        case id, text
        case appID = "app_id"
        case appName = "app_name"
        case correctionsApplied = "corrections_applied"
    }
}

struct CorrectionRule: Codable, Equatable, Identifiable, Sendable {
    let id: String
    let source: String
    let replacement: String
    let appID: String?
    let createdAt: String

    var scopeLabel: String {
        if let appID, !appID.isEmpty { return appID }
        return "Everywhere"
    }

    enum CodingKeys: String, CodingKey {
        case id, source, replacement
        case appID = "app_id"
        case createdAt = "created_at"
    }
}

struct CorrectionSuggestion: Codable, Equatable, Identifiable, Sendable {
    let source: String
    let replacement: String

    var id: String { "\(source.utf8.count):\(source)\(replacement)" }
}

enum CorrectionScope: String, CaseIterable, Identifiable, Sendable {
    case app
    case global

    var id: String { rawValue }
    var title: String { self == .app ? "This app" : "Everywhere" }
}
