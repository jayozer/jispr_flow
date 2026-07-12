import SwiftUI

struct SectionCard<Content: View>: View {
    let title: String
    let subtitle: String?
    @ViewBuilder let content: Content

    init(
        _ title: String,
        subtitle: String? = nil,
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.subtitle = subtitle
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.headline)
                    .foregroundStyle(JiSprTheme.ink)
                if let subtitle {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .jisprCard()
    }
}

struct SettingRow<Control: View>: View {
    let title: String
    let help: String?
    let source: String
    let editable: Bool
    @ViewBuilder let control: Control

    init(
        _ title: String,
        help: String? = nil,
        source: String,
        editable: Bool,
        @ViewBuilder control: () -> Control
    ) {
        self.title = title
        self.help = help
        self.source = source
        self.editable = editable
        self.control = control()
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 18) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(title)
                        .foregroundStyle(JiSprTheme.ink)
                    if !editable {
                        Image(systemName: "lock.fill")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .help("Controlled by \(source)")
                    }
                }
                if let help {
                    Text(help)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(width: 220, alignment: .leading)

            control
                .disabled(!editable)
                .frame(maxWidth: .infinity, alignment: .trailing)
                // Preserve the existing control column after removing the
                // visible provenance pill from the right-hand side.
                .padding(.trailing, 72)
        }
    }
}

struct StatusHero: View {
    let store: AppStore

    var body: some View {
        HStack(spacing: 16) {
            ZStack {
                Circle()
                    .fill(statusColor.opacity(0.16))
                    .frame(width: 58, height: 58)
                Image(systemName: store.state.symbol)
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundStyle(statusColor)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(store.statusTitle)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(JiSprTheme.ink)
                Text(store.stateDetail)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Spacer()
            if store.state == .recording {
                ProgressView(value: store.audioLevel)
                    .progressViewStyle(.linear)
                    .tint(JiSprTheme.orange)
                    .frame(width: 130)
            }
            Button(store.dictationEnabled ? "Pause" : "Start") {
                store.toggleDictation()
            }
            .buttonStyle(.borderedProminent)
            .tint(JiSprTheme.sage)
            .disabled(!store.isHostOnline)
        }
        .jisprCard()
    }

    private var statusColor: Color {
        switch store.state {
        case .recording, .warning: JiSprTheme.orange
        case .error, .offline: .secondary
        default: JiSprTheme.sage
        }
    }
}

struct DetailPage<Content: View>: View {
    let title: String
    let subtitle: String
    @ViewBuilder let content: Content

    init(_ title: String, subtitle: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.subtitle = subtitle
        self.content = content()
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.largeTitle.weight(.bold))
                        .foregroundStyle(JiSprTheme.ink)
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                content
            }
            .padding(24)
            .frame(maxWidth: 780, alignment: .leading)
        }
        .background(JiSprTheme.canvas)
    }
}
