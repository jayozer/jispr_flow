import Foundation

final class EngineProcessService: @unchecked Sendable {
    typealias MessageHandler = @Sendable (HostMessage) -> Void
    typealias DiagnosticHandler = @Sendable (String) -> Void
    typealias TerminationHandler = @Sendable (Int32) -> Void

    private let queue = DispatchQueue(label: "com.acrobat.jispr.engine")
    private var process: Process?
    private var inputHandle: FileHandle?
    private var outputBuffer = Data()
    private let decoder = JSONDecoder()
    private var onMessage: MessageHandler?
    private var onDiagnostic: DiagnosticHandler?
    private var onTermination: TerminationHandler?

    var isRunning: Bool {
        queue.sync { process?.isRunning == true }
    }

    func start(
        location: EngineLocation,
        onMessage: @escaping MessageHandler,
        onDiagnostic: @escaping DiagnosticHandler,
        onTermination: @escaping TerminationHandler
    ) throws {
        if isRunning { return }

        let process = Process()
        let inputPipe = Pipe()
        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.executableURL = location.executableURL
        process.arguments = ["app-host"]
        process.currentDirectoryURL = location.workingDirectoryURL
        process.environment = Self.launchEnvironment(
            base: ProcessInfo.processInfo.environment
        )
        process.standardInput = inputPipe
        process.standardOutput = outputPipe
        process.standardError = errorPipe

        queue.sync {
            self.onMessage = onMessage
            self.onDiagnostic = onDiagnostic
            self.onTermination = onTermination
            self.process = process
            self.inputHandle = inputPipe.fileHandleForWriting
            self.outputBuffer.removeAll(keepingCapacity: true)
        }

        outputPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self?.queue.async { self?.consume(data) }
        }
        errorPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            self?.queue.async { self?.onDiagnostic?(text.trimmingCharacters(in: .whitespacesAndNewlines)) }
        }
        process.terminationHandler = { [weak self] process in
            self?.queue.async {
                outputPipe.fileHandleForReading.readabilityHandler = nil
                errorPipe.fileHandleForReading.readabilityHandler = nil
                self?.process = nil
                self?.inputHandle = nil
                self?.onTermination?(process.terminationStatus)
            }
        }

        do {
            try process.run()
        } catch {
            queue.sync {
                self.process = nil
                self.inputHandle = nil
            }
            throw error
        }
    }

    static func launchEnvironment(base: [String: String]) -> [String: String] {
        var environment = base
        let standardPaths = [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
        let existingPaths = base["PATH", default: ""]
            .split(separator: ":")
            .map(String.init)
        environment["PATH"] = (standardPaths + existingPaths).reduce(into: []) {
            paths, path in
            if !path.isEmpty, !paths.contains(path) {
                paths.append(path)
            }
        }.joined(separator: ":")
        return environment
    }

    @discardableResult
    func send(command: String, payload: [String: Any] = [:]) -> String {
        let id = UUID().uuidString
        let message: [String: Any] = [
            "v": 1,
            "id": id,
            "command": command,
            "payload": payload,
        ]
        queue.async { [weak self] in
            guard let self, let handle = self.inputHandle else { return }
            do {
                var data = try JSONSerialization.data(withJSONObject: message)
                data.append(0x0A)
                try handle.write(contentsOf: data)
            } catch {
                self.onDiagnostic?("Could not send \(command): \(error.localizedDescription)")
            }
        }
        return id
    }

    func stop(forceAfter delay: TimeInterval = 1.5) {
        guard isRunning else { return }
        _ = send(command: "shutdown")
        queue.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let process = self?.process, process.isRunning else { return }
            process.terminate()
        }
    }

    private func consume(_ data: Data) {
        outputBuffer.append(data)
        while let newline = outputBuffer.firstIndex(of: 0x0A) {
            let line = outputBuffer[..<newline]
            outputBuffer.removeSubrange(...newline)
            guard !line.isEmpty else { continue }
            do {
                let message = try decoder.decode(HostMessage.self, from: Data(line))
                onMessage?(message)
            } catch {
                let raw = String(data: line, encoding: .utf8) ?? "(unreadable)"
                onDiagnostic?("Invalid engine message: \(error.localizedDescription) — \(raw)")
            }
        }
    }
}
