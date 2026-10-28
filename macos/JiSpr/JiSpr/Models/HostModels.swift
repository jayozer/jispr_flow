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
    let corrections: [CorrectionRule]
    let correctionsError: String?

    enum CodingKeys: String, CodingKey {
        case configPath = "config_path"
        case dataDir = "data_dir"
        case settings, options, presets, styles, transforms, dictionary, aliases, corrections
        case correctionsError = "corrections_error"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        configPath = try values.decodeIfPresent(String.self, forKey: .configPath)
        dataDir = try values.decode(String.self, forKey: .dataDir)
        settings = try values.decode([String: SettingValue].self, forKey: .settings)
        options = try values.decode([String: [String]].self, forKey: .options)
        presets = try values.decode([String: [String: JSONValue]].self, forKey: .presets)
        styles = try values.decode([String].self, forKey: .styles)
        transforms = try values.decode([String].self, forKey: .transforms)
        dictionary = try values.decode([DictionaryEntry].self, forKey: .dictionary)
        aliases = try values.decode([String: String].self, forKey: .aliases)
        corrections = try values.decodeIfPresent([CorrectionRule].self, forKey: .corrections) ?? []
        correctionsError = try values.decodeIfPresent(String.self, forKey: .correctionsError)
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
    let dictation: DictationRecord?
    let download: DownloadProgress?
    let running: Bool?
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

    var fallbackDetail: String {
        switch self {
        case .offline: "Dictation paused"
        case .idle: "Ready for your hotkey"
        case .recording, .preview: "Listening to your microphone"
        case .processing: "Transcribing your speech"
        case .inserted: "Text inserted"
        case .warning: "JiSpr needs attention"
        case .error: "The dictation engine reported an error"
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
