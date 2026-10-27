import Foundation

/// Frames stderr diagnostics without assuming that pipe reads align with lines
/// or UTF-8 characters. Oversized lines retain a bounded prefix until newline.
struct EngineDiagnosticBuffer {
    private let maximumLineBytes: Int
    private var pending = Data()
    private var truncated = false

    init(maximumLineBytes: Int = 64 * 1024) {
        precondition(maximumLineBytes > 0)
        self.maximumLineBytes = maximumLineBytes
    }

    mutating func append(_ data: Data) -> [String] {
        var lines: [String] = []
        for byte in data {
            if byte == 0x0A {
                if let line = takeLine() { lines.append(line) }
            } else if pending.count < maximumLineBytes {
                pending.append(byte)
            } else {
                truncated = true
            }
        }
        return lines
    }

    /// Flushes one final unterminated line; repeated EOF/termination is harmless.
    mutating func finish() -> [String] {
        takeLine().map { [$0] } ?? []
    }

    mutating func reset() {
        pending.removeAll(keepingCapacity: true)
        truncated = false
    }

    private mutating func takeLine() -> String? {
        let text = String(decoding: pending, as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let wasTruncated = truncated
        reset()
        guard !text.isEmpty || wasTruncated else { return nil }
        return wasTruncated ? text + " [diagnostic truncated]" : text
    }
}
