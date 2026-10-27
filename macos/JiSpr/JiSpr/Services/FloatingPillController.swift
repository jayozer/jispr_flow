import AppKit
import SwiftUI

struct FloatingPillConfiguration: Equatable {
    let enabled: Bool
    let style: String
    let state: DictationState
    let detail: String
    let audioLevel: Double
    let hotkey: String
    let position: String

    init(
        enabled: Bool,
        style: String,
        state: DictationState,
        detail: String,
        audioLevel: Double,
        hotkey: String,
        position: String = "bottom"
    ) {
        self.enabled = enabled
        self.style = style
        self.state = state
        self.detail = detail
        self.audioLevel = audioLevel
        self.hotkey = hotkey
        self.position = position
    }

    var showsLivingCaret: Bool {
        position == "caret"
            && (state == .recording || state == .preview
                || state == .processing || state == .inserted)
    }
}

struct FloatingPillPresentation: Equatable {
    enum Kind: Equatable {
        case idle
        case recording
        case processing
        case inserted
        case warning
        case error
        case offline
    }

    let kind: Kind
    let label: String
    let detail: String
    let width: CGFloat
    let height: CGFloat
    let showsMeter: Bool

    init(configuration: FloatingPillConfiguration) {
        let expanded = configuration.style == "expanded"
        let displayKey = configuration.hotkey.lowercased() == "fn"
            ? "Fn"
            : configuration.hotkey.uppercased()

        switch configuration.state {
        case .idle:
            kind = .idle
            label = "Ready · Hold \(displayKey)"
            detail = "Ready for dictation"
        case .recording, .preview:
            kind = .recording
            label = "Listening"
            detail = "Release \(displayKey) when you are done"
        case .processing:
            kind = .processing
            label = "Transcribing…"
            detail = "Turning speech into text"
        case .inserted:
            kind = .inserted
            label = "Inserted"
            detail = "Text added to the focused app"
        case .warning:
            kind = .warning
            label = configuration.detail.isEmpty ? "Needs attention" : configuration.detail
            detail = "Open JiSpr for details"
        case .error:
            kind = .error
            label = configuration.detail.isEmpty ? "Engine error" : configuration.detail
            detail = "Open JiSpr for details"
        case .offline:
            kind = .offline
            label = "Dictation paused"
            detail = "Start JiSpr from the menu bar"
        }

        showsMeter = configuration.state == .recording || configuration.state == .preview
        if configuration.showsLivingCaret {
            width = expanded ? 160 : 72
            height = expanded ? 32 : 28
        } else if expanded {
            width = 360
            height = 66
        } else {
            switch kind {
            case .idle:
                width = 76
                height = 10
            case .warning, .error:
                width = 280
                height = 40
            default:
                width = 148
                height = 34
            }
        }
    }
}

enum PillTransition: Equatable {
    case hide
    case apply
    case delayIdle
    case coalesceIdle
}

/// Even an accidental call to makeKey() must leave the editor as first responder.
final class DictationStatusPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class FloatingPillController {
    private let panel: NSPanel
    private let hostingController: NSHostingController<FloatingPillView>
    private var displayedConfiguration: FloatingPillConfiguration?
    private var transitionRevision = 0
    private var pendingIdleConfiguration: FloatingPillConfiguration?
    private let caretTracker: CaretTracker
    private var caretBounds: CGRect?

    init(caretProvider: any CaretBoundsProviding = AccessibilityCaretBoundsProvider()) {
        caretTracker = CaretTracker(provider: caretProvider)
        let initial = FloatingPillConfiguration(
            enabled: false,
            style: "compact",
            state: .offline,
            detail: "",
            audioLevel: 0,
            hotkey: "fn"
        )
        hostingController = NSHostingController(
            rootView: FloatingPillView(configuration: initial)
        )
        panel = DictationStatusPanel(
            contentRect: .zero,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.contentViewController = hostingController
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .floating
        panel.ignoresMouseEvents = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [
            .moveToActiveSpace,
            .fullScreenAuxiliary,
        ]
        panel.setAccessibilityLabel("JiSpr dictation status")
        caretTracker.onChange = { [weak self] bounds in
            guard let self else { return }
            self.caretBounds = bounds
            self.positionPanel()
        }
    }

    /// Pure decision for `update(_:)`: `inserted`/`warning`/`error` are held
    /// on screen for a beat before idle replaces them. While that timer runs,
    /// further idle refreshes must coalesce into it rather than re-arm it --
    /// hands-free mode repeats the idle state on every audio-level frame
    /// (~30ms apart), so rescheduling per refresh would postpone the return
    /// to idle indefinitely.
    nonisolated static func transition(
        for configuration: FloatingPillConfiguration,
        displayed: FloatingPillConfiguration?,
        idlePending: Bool
    ) -> PillTransition {
        guard configuration.enabled else { return .hide }
        if let displayed, displayed.position != configuration.position { return .apply }
        if configuration.state == .idle,
           let displayed,
           displayed.state == .inserted
                || displayed.state == .warning
                || displayed.state == .error {
            return idlePending ? .coalesceIdle : .delayIdle
        }
        return .apply
    }

    func update(_ configuration: FloatingPillConfiguration) {
        switch Self.transition(
            for: configuration,
            displayed: displayedConfiguration,
            idlePending: pendingIdleConfiguration != nil
        ) {
        case .hide:
            transitionRevision += 1
            pendingIdleConfiguration = nil
            displayedConfiguration = nil
            caretTracker.stop()
            caretBounds = nil
            panel.orderOut(nil)
        case .coalesceIdle:
            // Keep the running timer (and its revision); only refresh the
            // configuration it will show when it fires.
            pendingIdleConfiguration = configuration
        case .delayIdle:
            transitionRevision += 1
            let revision = transitionRevision
            pendingIdleConfiguration = configuration
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.9) { [weak self] in
                guard let self, self.transitionRevision == revision else { return }
                guard let pending = self.pendingIdleConfiguration else { return }
                self.pendingIdleConfiguration = nil
                self.apply(pending)
            }
        case .apply:
            transitionRevision += 1
            pendingIdleConfiguration = nil
            apply(configuration)
        }
    }

    func close() {
        transitionRevision += 1
        pendingIdleConfiguration = nil
        displayedConfiguration = nil
        caretTracker.stop()
        caretBounds = nil
        panel.orderOut(nil)
        panel.close()
    }

    private func apply(_ configuration: FloatingPillConfiguration) {
        displayedConfiguration = configuration
        if configuration.position == "caret", configuration.state.isBusy {
            if !caretTracker.isRunning {
                caretBounds = nil
            }
            caretTracker.start()
        } else {
            // Keep the last position for the brief success confirmation.
            caretTracker.stop()
            if configuration.state != .inserted {
                caretBounds = nil
            }
        }
        if Self.hidesAtRest(configuration) {
            panel.orderOut(nil)
            return
        }

        positionPanel()
    }

    nonisolated static func hidesAtRest(_ configuration: FloatingPillConfiguration) -> Bool {
        configuration.position == "caret"
            && (configuration.state == .idle || configuration.state == .offline)
    }

    private func positionPanel() {
        guard let configuration = displayedConfiguration,
              configuration.enabled,
              !Self.hidesAtRest(configuration) else { return }
        let presentation = FloatingPillPresentation(configuration: configuration)
        let size = NSSize(width: presentation.width, height: presentation.height)
        let screens = NSScreen.screens
        let caretFrame: CGRect?
        if configuration.showsLivingCaret, let caretBounds, let primary = screens.first {
            caretFrame = CaretPlacement.frame(
                caretBounds: caretBounds,
                size: size,
                screens: screens.map(\.frame),
                visibleFrames: screens.map(\.visibleFrame),
                primaryScreenHeight: primary.frame.height
            )
        } else {
            caretFrame = nil
        }
        if let caretFrame {
            setPanelFrame(caretFrame, configuration: configuration)
            return
        }

        // An unavailable/unsafe caret gets the full existing status pill.
        let fallback = FloatingPillConfiguration(
            enabled: configuration.enabled,
            style: configuration.style,
            state: configuration.state,
            detail: configuration.detail,
            audioLevel: configuration.audioLevel,
            hotkey: configuration.hotkey
        )
        let fallbackPresentation = FloatingPillPresentation(configuration: fallback)
        let fallbackSize = NSSize(
            width: fallbackPresentation.width,
            height: fallbackPresentation.height
        )
        let mouseLocation = NSEvent.mouseLocation
        let pointerScreen = screens.first { $0.frame.contains(mouseLocation) }
        let screen = pointerScreen ?? NSApp.keyWindow?.screen ?? NSScreen.main
        guard let visibleFrame = screen?.visibleFrame else {
            panel.orderOut(nil)
            return
        }
        let frame = NSRect(
            x: visibleFrame.midX - fallbackSize.width / 2,
            y: visibleFrame.minY + 18,
            width: fallbackSize.width,
            height: fallbackSize.height
        )
        setPanelFrame(frame, configuration: fallback)
    }

    private func setPanelFrame(_ frame: NSRect, configuration: FloatingPillConfiguration) {
        hostingController.rootView = FloatingPillView(configuration: configuration)
        hostingController.view.frame = NSRect(origin: .zero, size: frame.size)
        // NSPanel frame animation snapshots the old backing surface while the
        // SwiftUI host is already rendering the new size. During the compact
        // active -> idle transition that briefly looks like two stacked pills.
        // Switch the borderless window atomically; SwiftUI still animates the
        // live microphone meter inside the single panel.
        panel.setFrame(frame, display: true, animate: false)
        panel.orderFrontRegardless()
    }
}

struct FloatingPillView: View {
    let configuration: FloatingPillConfiguration

    private var presentation: FloatingPillPresentation {
        FloatingPillPresentation(configuration: configuration)
    }

    var body: some View {
        Group {
            if configuration.showsLivingCaret {
                LivingCaretView(configuration: configuration)
            } else if configuration.style == "expanded" {
                expandedPill
            } else if presentation.kind == .idle {
                idlePill
            } else {
                compactPill
            }
        }
        .frame(width: presentation.width, height: presentation.height)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(presentation.label)
        .accessibilityValue(presentation.detail)
    }

    private var idlePill: some View {
        Capsule()
            .fill(.black.opacity(0.80))
            .overlay {
                Capsule()
                    .fill(JiSprTheme.sage)
                    .frame(width: 40, height: 3)
            }
            .overlay { Capsule().stroke(.white.opacity(0.14), lineWidth: 0.5) }
    }

    private var compactPill: some View {
        HStack(spacing: 9) {
            statusSymbol
            if presentation.showsMeter {
                levelMeter(maximumHeight: 18)
            } else {
                Text(presentation.label)
                    .font(.system(size: 12, weight: .semibold))
                    .lineLimit(1)
            }
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 13)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.black.opacity(0.86), in: Capsule())
        .overlay { Capsule().stroke(.white.opacity(0.14), lineWidth: 0.75) }
    }

    private var expandedPill: some View {
        HStack(spacing: 13) {
            statusSymbol
                .font(.system(size: 19, weight: .semibold))
                .frame(width: 30)

            VStack(alignment: .leading, spacing: 2) {
                Text(presentation.label)
                    .font(.system(size: 14, weight: .semibold))
                    .lineLimit(1)
                Text(presentation.detail)
                    .font(.system(size: 11))
                    .foregroundStyle(.white.opacity(0.66))
                    .lineLimit(1)
            }

            Spacer(minLength: 8)
            if presentation.showsMeter {
                levelMeter(maximumHeight: 28)
            }
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 18)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.black.opacity(0.88), in: RoundedRectangle(cornerRadius: 21))
        .overlay {
            RoundedRectangle(cornerRadius: 21)
                .stroke(.white.opacity(0.14), lineWidth: 0.75)
        }
    }

    @ViewBuilder
    private var statusSymbol: some View {
        switch presentation.kind {
        case .idle:
            Image(systemName: "waveform")
                .foregroundStyle(JiSprTheme.sage)
        case .recording:
            Circle()
                .fill(JiSprTheme.orange)
                .frame(width: 9, height: 9)
        case .processing:
            Image(systemName: "sparkles")
                .foregroundStyle(JiSprTheme.orange)
        case .inserted:
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(JiSprTheme.sage)
        case .warning:
            Image(systemName: "waveform.badge.exclamationmark")
                .foregroundStyle(JiSprTheme.orange)
        case .error:
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
        case .offline:
            Image(systemName: "waveform.slash")
                .foregroundStyle(.white.opacity(0.6))
        }
    }

    private func levelMeter(maximumHeight: CGFloat) -> some View {
        let pattern: [CGFloat] = [0.34, 0.62, 0.92, 0.70, 1.0, 0.58, 0.38]
        let energy = 0.18 + max(0, min(configuration.audioLevel, 1)) * 0.82
        return HStack(alignment: .center, spacing: 2.5) {
            ForEach(Array(pattern.enumerated()), id: \.offset) { _, weight in
                Capsule()
                    .fill(JiSprTheme.sage)
                    .frame(width: 3, height: max(3, maximumHeight * weight * energy))
            }
        }
        .frame(height: maximumHeight)
        .animation(.easeOut(duration: 0.08), value: configuration.audioLevel)
    }
}
