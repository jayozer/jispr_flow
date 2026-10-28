import Foundation
@testable import JiSpr

enum SetupTestFixtures {
    static let englishID = "whisper-small-en-mlx"
    static let multilingualID = "parakeet-v3"

    static func catalog(
        installed: Bool = true, verified: Bool = true,
        currentID: String? = englishID, language: String = "en", polish: String? = "rules",
        canApply: Bool = true, notice: String? = nil,
        models: [JSONValue]? = nil, download: DownloadProgress? = nil
    ) -> [String: JSONValue] {
        var result: [String: JSONValue] = [
            "models": .array(models ?? [
                model(id: englishID, name: "Whisper Small English", languages: ["en"], installed: installed, verified: verified),
                model(id: multilingualID, name: "Parakeet v3", languages: ["auto", "en", "es"], installed: installed, verified: verified),
            ]),
            "current_model_id": currentID.map(JSONValue.string) ?? .null,
            "current_language": .string(language),
            "polish_backend": polish.map(JSONValue.string) ?? .null,
            "can_apply": .bool(canApply),
        ]
        if let notice { result["configuration_notice"] = .string(notice) }
        if let download { result["download"] = progressJSON(download) }
        return result
    }

    static func model(
        id: String, name: String, languages: [String], installed: Bool, verified: Bool,
        available: Bool = true
    ) -> JSONValue {
        .object([
            "id": .string(id), "name": .string(name),
            "description": .string(id == multilingualID
                ? "Fast multilingual dictation, running entirely on this Mac."
                : "A compact local model for everyday English dictation."),
            "backend": .string(id == multilingualID ? "mlx-parakeet" : "mlx-whisper"),
            "repo_id": .string("mlx-community/\(id)"),
            "languages": .array(languages.map(JSONValue.string)),
            "size_hint": .string(id == multilingualID ? "2.5 GB" : "481 MB"),
            "available": .bool(available), "installed": .bool(installed),
            "verified": .bool(verified),
            "unavailable_reason": available ? .null : .string("This backend is not installed."),
        ])
    }

    static func progress(
        job: String = "job-1", model: String = englishID,
        state: DownloadState = .downloading, bytes: Int64 = 25_000_000,
        total: Int64? = 100_000_000, error: String? = nil
    ) -> DownloadProgress {
        DownloadProgress(
            jobID: job, modelID: model, state: state, downloadedBytes: bytes,
            totalBytes: total, fileName: "weights.safetensors", error: error, localPath: nil
        )
    }

    static func progressJSON(_ value: DownloadProgress) -> JSONValue {
        var object: [String: JSONValue] = [
            "job_id": .string(value.jobID), "model_id": .string(value.modelID),
            "state": .string(value.state.rawValue),
            "downloaded_bytes": .int(Int(value.downloadedBytes)),
            "total_bytes": value.totalBytes.map { .int(Int($0)) } ?? .null,
        ]
        if let name = value.fileName { object["file_name"] = .string(name) }
        if let error = value.error { object["error"] = .string(error) }
        return .object(object)
    }
}

@MainActor
final class SetupTestCommands {
    struct Call {
        let id: String
        let command: String
        let payload: [String: Any]
    }
    var calls: [Call] = []
    func send(_ command: String, payload: [String: Any]) -> String {
        let id = "setup-\(calls.count + 1)"
        calls.append(Call(id: id, command: command, payload: payload))
        return id
    }
    func lastID(_ command: String) -> String {
        calls.last(where: { $0.command == command })!.id
    }
}
