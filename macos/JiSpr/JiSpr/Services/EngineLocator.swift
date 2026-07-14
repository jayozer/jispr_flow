import Foundation

enum EngineLocatorError: LocalizedError {
    case unavailable(String)

    var errorDescription: String? {
        switch self {
        case let .unavailable(path):
            "JiSpr could not find its local engine at \(path). Reinstall JiSpr, or run `uv sync --all-extras` when using a repository build."
        }
    }
}

struct EngineLocation: Equatable, Sendable {
    let executableURL: URL
    let workingDirectoryURL: URL?
}

enum EngineLocator {
    static func resolve(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        bundle: Bundle = .main
    ) throws -> EngineLocation {
        let environmentPath = environment["JISPR_ENGINE_PATH"]
        let bundledURL = bundle.resourceURL?.appendingPathComponent("engine/local-flow")
        let bundledPath = bundledURL.flatMap {
            FileManager.default.isExecutableFile(atPath: $0.path) ? $0.path : nil
        }
        let configuredPath = environmentPath
            ?? bundledPath
            ?? bundle.object(forInfoDictionaryKey: "JiSprEnginePath") as? String
            ?? ""
        let expandedPath = NSString(string: configuredPath).expandingTildeInPath
        guard !expandedPath.isEmpty,
              FileManager.default.isExecutableFile(atPath: expandedPath) else {
            throw EngineLocatorError.unavailable(expandedPath.isEmpty ? "(not configured)" : expandedPath)
        }

        let workingPath = environment["JISPR_WORKING_DIRECTORY"]
            ?? bundle.object(forInfoDictionaryKey: "JiSprWorkingDirectory") as? String
        let workingURL = workingPath.map {
            URL(fileURLWithPath: NSString(string: $0).expandingTildeInPath, isDirectory: true)
        } ?? (bundledPath == nil ? nil : FileManager.default.homeDirectoryForCurrentUser)
        return EngineLocation(
            executableURL: URL(fileURLWithPath: expandedPath),
            workingDirectoryURL: workingURL
        )
    }

    /// Whether the resolved engine lives inside the app bundle. A dev/repository
    /// build points at an out-of-bundle interpreter (e.g. `.venv`/Homebrew Python)
    /// via `JISPR_ENGINE_PATH`; macOS attributes the Fn event tap to that binary,
    /// so TCC grants to JiSpr.app do not reach it and the Fn hotkey cannot work.
    static func isBundled(engineURL: URL, resourceRoot: URL?) -> Bool {
        guard let resourceRoot else { return false }
        let engine = engineURL.standardizedFileURL.path
        let root = resourceRoot.standardizedFileURL.path
        let rootPrefix = root.hasSuffix("/") ? root : root + "/"
        return engine == root || engine.hasPrefix(rootPrefix)
    }
}
