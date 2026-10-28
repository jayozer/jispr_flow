import Foundation

enum SetupPhase: Int, CaseIterable, Identifiable {
    case welcome, models, permissions, practice, complete
    var id: Int { rawValue }
    var title: String {
        switch self {
        case .welcome: "Welcome"
        case .models: "Speech model"
        case .permissions: "Permissions"
        case .practice: "Try dictation"
        case .complete: "Ready"
        }
    }
}

enum SetupPermission: String, CaseIterable, Identifiable {
    case accessibility, inputMonitoring, microphone
    var id: String { rawValue }
    var title: String {
        switch self {
        case .accessibility: "Accessibility"
        case .inputMonitoring: "Input Monitoring"
        case .microphone: "Microphone"
        }
    }
    var detail: String {
        switch self {
        case .accessibility: "Insert your words into the app you're using."
        case .inputMonitoring: "Hear your chosen shortcut while you work in other apps."
        case .microphone: "Record your voice for private, on-device transcription."
        }
    }
}

struct SetupSpeechModel: Codable, Equatable, Identifiable, Sendable {
    let id: String
    let name: String
    let description: String
    let backend: String
    let repoID: String
    let languages: [String]
    let sizeHint: String
    let available: Bool
    let unavailableReason: String?
    let installed: Bool
    let verified: Bool
    let localPath: String?

    var isReady: Bool { available && installed && verified }

    enum CodingKeys: String, CodingKey {
        case id, name, description, backend, languages, available, installed, verified
        case repoID = "repo_id"
        case sizeHint = "size_hint"
        case unavailableReason = "unavailable_reason"
        case localPath = "local_path"
    }

    init(
        id: String, name: String, description: String, backend: String, repoID: String,
        languages: [String], sizeHint: String, available: Bool,
        unavailableReason: String? = nil, installed: Bool, verified: Bool = false,
        localPath: String? = nil
    ) {
        self.id = id
        self.name = name
        self.description = description
        self.backend = backend
        self.repoID = repoID
        self.languages = languages
        self.sizeHint = sizeHint
        self.available = available
        self.unavailableReason = unavailableReason
        self.installed = installed
        self.verified = verified
        self.localPath = localPath
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        name = try values.decode(String.self, forKey: .name)
        description = try values.decode(String.self, forKey: .description)
        backend = try values.decode(String.self, forKey: .backend)
        repoID = try values.decode(String.self, forKey: .repoID)
        languages = try values.decode([String].self, forKey: .languages)
        sizeHint = try values.decode(String.self, forKey: .sizeHint)
        available = try values.decode(Bool.self, forKey: .available)
        unavailableReason = try values.decodeIfPresent(String.self, forKey: .unavailableReason)
        installed = try values.decode(Bool.self, forKey: .installed)
        verified = try values.decodeIfPresent(Bool.self, forKey: .verified) ?? false
        localPath = try values.decodeIfPresent(String.self, forKey: .localPath)
    }
}

enum DownloadState: String, Codable, Sendable {
    case preparing, downloading, cancelling, cancelled, completed, error
    var isActive: Bool { self == .preparing || self == .downloading || self == .cancelling }
    var isTerminal: Bool { !isActive }
    var title: String {
        switch self {
        case .preparing: "Preparing model…"
        case .downloading: "Downloading model…"
        case .cancelling: "Cancelling…"
        case .cancelled: "Download cancelled"
        case .completed: "Model files ready"
        case .error: "Model needs attention"
        }
    }
}

struct DownloadProgress: Codable, Equatable, Sendable {
    let jobID: String
    let modelID: String
    var state: DownloadState
    var downloadedBytes: Int64
    let totalBytes: Int64?
    let fileName: String?
    let error: String?
    let localPath: String?

    var fractionCompleted: Double? {
        guard let totalBytes, totalBytes > 0 else { return nil }
        return min(1, max(0, Double(downloadedBytes) / Double(totalBytes)))
    }
    var byteLabel: String {
        let received = ByteCountFormatter.string(fromByteCount: downloadedBytes, countStyle: .file)
        guard let totalBytes, totalBytes > 0 else { return "\(received) received" }
        return "\(received) of \(ByteCountFormatter.string(fromByteCount: totalBytes, countStyle: .file))"
    }

    enum CodingKeys: String, CodingKey {
        case state, error
        case jobID = "job_id"
        case modelID = "model_id"
        case downloadedBytes = "downloaded_bytes"
        case totalBytes = "total_bytes"
        case fileName = "file_name"
        case localPath = "local_path"
    }
}

struct SetupCatalog: Decodable, Equatable, Sendable {
    let models: [SetupSpeechModel]
    let currentModelID: String?
    let currentLanguage: String
    let polishBackend: String?
    let canApply: Bool
    let configurationNotice: String?
    let download: DownloadProgress?

    enum CodingKeys: String, CodingKey {
        case models, download
        case currentModelID = "current_model_id"
        case currentLanguage = "current_language"
        case polishBackend = "polish_backend"
        case canApply = "can_apply"
        case configurationNotice = "configuration_notice"
    }
}
