import SwiftUI

/// A passive status surface: it never accepts focus or edits the target field.
struct LivingCaretView: View {
    let configuration: FloatingPillConfiguration
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var presentation: FloatingPillPresentation {
        FloatingPillPresentation(configuration: configuration)
    }

    var body: some View {
        HStack(spacing: 7) {
            switch presentation.kind {
            case .recording:
                Image(systemName: "mic.fill")
                    .foregroundStyle(JiSprTheme.orange)
                meter
            case .processing:
                Image(systemName: "sparkles")
                    .foregroundStyle(JiSprTheme.orange)
                    .symbolEffect(.pulse, options: .repeating, isActive: !reduceMotion)
            case .inserted:
                Image(systemName: "checkmark")
                    .foregroundStyle(JiSprTheme.sage)
            default:
                Image(systemName: "waveform")
                    .foregroundStyle(JiSprTheme.sage)
            }

            if configuration.style == "expanded" {
                Text(presentation.label)
                    .font(.system(size: 11, weight: .medium))
                    .lineLimit(1)
            }
        }
        .font(.system(size: 11, weight: .semibold))
        .padding(.horizontal, 9)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.regularMaterial, in: Capsule())
        .overlay { Capsule().stroke(.primary.opacity(0.12), lineWidth: 0.75) }
    }

    private var meter: some View {
        let level = configuration.audioLevel.isFinite
            ? max(0, min(configuration.audioLevel, 1)) : 0
        let weights: [CGFloat] = [0.5, 0.85, 1, 0.65]
        return HStack(spacing: 2) {
            ForEach(Array(weights.enumerated()), id: \.offset) { _, weight in
                Capsule()
                    .fill(JiSprTheme.sage)
                    .frame(width: 2.5, height: max(3, 14 * weight * (0.2 + level * 0.8)))
            }
        }
        .frame(height: 14)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.1), value: level)
    }
}
