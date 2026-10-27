import Darwin
import Foundation

final class EngineProcessService: @unchecked Sendable {
    typealias MessageHandler = @Sendable (HostMessage) -> Void
    typealias DiagnosticHandler = @Sendable (String) -> Void
    typealias TerminationHandler = @Sendable (Int32) -> Void

    private let queue = DispatchQueue(label: "com.acrobat.jispr.engine")
    private var process: Process?
    private var inputHandle: FileHandle?
    private var outputBuffer = Data()
    private var diagnosticBuffer = EngineDiagnosticBuffer()
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
        try Self.makeNonblocking(errorPipe.fileHandleForReading)

        queue.sync {
            self.onMessage = onMessage
            self.onDiagnostic = onDiagnostic
            self.onTermination = onTermination
            self.process = process
            self.inputHandle = inputPipe.fileHandleForWriting
            self.outputBuffer.removeAll(keepingCapacity: true)
            self.diagnosticBuffer.reset()
        }

        outputPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self?.queue.async { [weak self] in
                guard let self, self.process === process else { return }
                self.consume(data)
            }
        }
        errorPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            self?.queue.async { [weak self] in
                guard let self, self.process === process else { return }
                self.readDiagnostics(from: handle)
            }
        }
        process.terminationHandler = { [weak self] process in
            self?.queue.async { [weak self] in
                guard let self, self.process === process else { return }
                outputPipe.fileHandleForReading.readabilityHandler = nil
                errorPipe.fileHandleForReading.readabilityHandler = nil
                // A termination callback can precede the final readability
                // callback. Read remaining bytes on the same queue before
                // flushing, without waiting for a descendant to close stderr.
                self.readDiagnostics(from: errorPipe.fileHandleForReading)
                self.flushDiagnostics()
                try? errorPipe.fileHandleForReading.close()
                self.process = nil
                self.inputHandle = nil
                self.onTermination?(process.terminationStatus)
            }
        }

        do {
            try process.run()
        } catch {
            outputPipe.fileHandleForReading.readabilityHandler = nil
            errorPipe.fileHandleForReading.readabilityHandler = nil
            queue.sync {
                self.process = nil
                self.inputHandle = nil
                self.diagnosticBuffer.reset()
            }
            throw error
        }
    }

    static func launchEnvironment(base: [String: String]) -> [String: String] {
        var environment = base
        // The engine lives inside the signed app bundle; letting Python write
        // __pycache__/*.pyc there adds unsealed files and fails strict
        // code-sign verification after the first run.
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
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

    private static func makeNonblocking(_ handle: FileHandle) throws {
        let descriptor = handle.fileDescriptor
        let flags = fcntl(descriptor, F_GETFL)
        guard flags != -1, fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) != -1 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
    }

    /// Called only on `queue`, including the final drain after process exit.
    private func readDiagnostics(from handle: FileHandle) {
        var bytes = [UInt8](repeating: 0, count: 4096)
        while true {
            let count = bytes.withUnsafeMutableBytes { storage in
                Darwin.read(handle.fileDescriptor, storage.baseAddress, storage.count)
            }
            if count > 0 {
                for line in diagnosticBuffer.append(Data(bytes.prefix(count))) {
                    onDiagnostic?(line)
                }
            } else if count == 0 {
                handle.readabilityHandler = nil
                flushDiagnostics()
                return
            } else {
                let errorCode = errno
                if errorCode == EINTR { continue }
                if errorCode != EAGAIN, errorCode != EWOULDBLOCK {
                    handle.readabilityHandler = nil
                    flushDiagnostics()
                    let error = POSIXError(POSIXErrorCode(rawValue: errorCode) ?? .EIO)
                    onDiagnostic?("Could not read engine diagnostics: \(error.localizedDescription)")
                }
                return
            }
        }
    }

    private func flushDiagnostics() {
        for line in diagnosticBuffer.finish() {
            onDiagnostic?(line)
        }
    }
}
