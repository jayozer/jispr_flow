import Foundation

/// Poll only during active dictation. AX work runs in the provider's actor;
/// cancellation/revisions keep a slow response from reviving a hidden panel.
@MainActor
final class CaretTracker {
    var onChange: ((CGRect?) -> Void)?
    private let provider: any CaretBoundsProviding
    private let interval: Duration
    private var task: Task<Void, Never>?
    private var revision = 0

    var isRunning: Bool { task != nil }

    init(provider: any CaretBoundsProviding, interval: Duration = .milliseconds(250)) {
        self.provider = provider
        self.interval = interval
    }

    func start() {
        guard task == nil else { return }
        revision += 1
        let currentRevision = revision
        let provider = provider
        let interval = interval
        task = Task { [weak self] in
            while !Task.isCancelled {
                let bounds = await provider.currentBounds()
                guard !Task.isCancelled else { return }
                self?.deliver(bounds, revision: currentRevision)
                do {
                    try await Task.sleep(for: interval)
                } catch {
                    return
                }
            }
        }
    }

    func stop() {
        revision += 1
        task?.cancel()
        task = nil
    }

    private func deliver(_ bounds: CGRect?, revision: Int) {
        guard revision == self.revision, task != nil else { return }
        onChange?(bounds)
    }

    deinit {
        task?.cancel()
    }
}
