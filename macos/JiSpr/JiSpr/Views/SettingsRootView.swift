import SwiftUI

struct SettingsRootView: View {
    let store: AppStore
    @State private var selection: SettingsSection? = .general

    var body: some View {
        NavigationSplitView {
            List(SettingsSection.allCases, selection: $selection) { section in
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(section.title)
                            .lineLimit(1)
                        Text(section.subtitle)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                } icon: {
                    Image(systemName: section.symbol)
                        .foregroundStyle(.secondary)
                        .frame(width: 18)
                }
                .tag(section)
            }
            .listStyle(.sidebar)
            .navigationTitle("JiSpr")
            .navigationSplitViewColumnWidth(min: 190, ideal: 215, max: 260)
        } detail: {
            Group {
                switch selection ?? .general {
                case .general: GeneralSettingsView(store: store)
                case .models: ModelSettingsView(store: store)
                case .writing: WritingSettingsView(store: store)
                case .appearance: AppearanceSettingsView(store: store)
                case .personalization: PersonalizationSettingsView(store: store)
                case .advanced: AdvancedSettingsView(store: store)
                }
            }
            .safeAreaInset(edge: .bottom) {
                if store.bannerMessage != nil || store.hasUnsavedChanges {
                    SaveBar(store: store)
                }
            }
        }
        .navigationSplitViewStyle(.balanced)
        .frame(minWidth: 820, idealWidth: 940, minHeight: 600, idealHeight: 700)
        // The warm pastel palette is the product's chosen appearance, not a
        // system-light/dark adaptation. The menu-bar glyph still follows macOS.
        .preferredColorScheme(.light)
    }
}

private struct SaveBar: View {
    let store: AppStore

    var body: some View {
        HStack(spacing: 12) {
            if let message = store.bannerMessage {
                Image(systemName: store.state == .error ? "exclamationmark.triangle" : "info.circle")
                    .foregroundStyle(store.state == .error ? JiSprTheme.orange : JiSprTheme.sage)
                Text(message)
                    .font(.callout)
                    .lineLimit(2)
            }
            Spacer()
            if store.hasUnsavedChanges {
                Button("Revert") { store.discardChanges() }
                Button("Save Changes") { store.saveSettings() }
                    .buttonStyle(.borderedProminent)
                    .tint(JiSprTheme.orange)
                    .keyboardShortcut(.return)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(.regularMaterial)
        .overlay(alignment: .top) { Divider() }
    }
}
