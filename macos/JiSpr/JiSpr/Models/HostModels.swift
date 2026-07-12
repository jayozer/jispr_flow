import Foundation

struct SettingValue: Codable, Equatable, Sendable {
    let value: JSONValue
    let source: String
    let editable: Bool
}

struct DictionaryEntry: Codable, Equatable, Identifiable, Sendable {
    let term: String
    let starred: Bool?
    let uses: Int?

    var id: String { term }
}

struct HostSnapshot: Codable, Equatable, Sendable {
    let configPath: String?
    let dataDir: String
    let settings: [String: SettingValue]
    let options: [String: [String]]
    let presets: [String: [String: JSONValue]]
    let styles: [String]
    let transforms: [String]
    let dictionary: [DictionaryEntry]
    let aliases: [String: String]

    enum CodingKeys: String, CodingKey {
        case configPath = "config_path"
        case dataDir = "data_dir"
        case settings, options, presets, styles, transforms, dictionary, aliases
    }
}

struct HostMessage: Codable, Equatable, Sendable {
    let v: Int
    let event: String
    let id: String?
    let ok: Bool?
    let `protocol`: Int?
    let state: String?
    let detail: String?
    let level: Double?
    let snapshot: HostSnapshot?
    let result: [String: JSONValue]?
    let message: String?
    let hint: String?
}

enum DictationState: String, Sendable {
    case offline
    case idle
    case recording
    case processing
    case preview
    case inserted
    case warning
    case error

    init(hostValue: String) {
        self = DictationState(rawValue: hostValue) ?? .error
    }

    var title: String {
        switch self {
        case .offline: "Engine offline"
        case .idle: "Ready"
        case .recording: "Listening"
        case .processing: "Polishing"
        case .preview: "Listening"
        case .inserted: "Inserted"
        case .warning: "Needs attention"
        case .error: "Engine error"
        }
    }

    var symbol: String {
        switch self {
        case .offline: "waveform.slash"
        case .idle: "waveform"
        case .recording: "waveform.circle.fill"
        case .processing, .preview: "sparkles"
        case .inserted: "checkmark.circle.fill"
        case .warning: "waveform.badge.exclamationmark"
        case .error: "exclamationmark.triangle.fill"
        }
    }

    var isBusy: Bool {
        self == .recording || self == .processing || self == .preview
    }
}

enum SettingsSection: String, CaseIterable, Identifiable, Sendable {
    case general
    case models
    case writing
    case appearance
    case personalization
    case advanced

    var id: String { rawValue }

    var title: String {
        rawValue.capitalized
    }

    var symbol: String {
        switch self {
        case .general: "slider.horizontal.3"
        case .models: "cpu"
        case .writing: "text.badge.checkmark"
        case .appearance: "paintpalette"
        case .personalization: "person.crop.circle.badge.checkmark"
        case .advanced: "gearshape.2"
        }
    }

    var subtitle: String {
        switch self {
        case .general: "Capture and startup"
        case .models: "Speech and polish"
        case .writing: "Style and insertion"
        case .appearance: "Pill and color"
        case .personalization: "Words and aliases"
        case .advanced: "VAD and recovery"
        }
    }
}
