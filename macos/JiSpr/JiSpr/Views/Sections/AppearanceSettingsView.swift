import SwiftUI

struct AppearanceSettingsView: View {
    let store: AppStore

    private var pillEnabled: Bool { store.boolValue("floating_pill") }
    private var pillStyle: String { store.stringValue("pill_style") }

    var body: some View {
        DetailPage("Appearance", subtitle: "Shape the visual feedback you see while speaking.") {
            SectionCard(
                "On-screen feedback",
                subtitle: "A small indicator near the bottom center of your display"
            ) {
                HStack(spacing: 14) {
                    ZStack {
                        Circle()
                            .fill(JiSprTheme.sage.opacity(0.16))
                            .frame(width: 44, height: 44)
                        Image(systemName: "waveform")
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundStyle(JiSprTheme.sage)
                    }

                    VStack(alignment: .leading, spacing: 3) {
                        Text("Recording pill")
                            .font(.headline)
                            .foregroundStyle(JiSprTheme.ink)
                        Text(pillEnabled ? "Visible while JiSpr listens and processes" : "Hidden")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Spacer()

                    Toggle("Recording pill", isOn: store.boolBinding("floating_pill"))
                        .labelsHidden()
                        .toggleStyle(.switch)
                        .controlSize(.large)
                        .disabled(!store.isEditable("floating_pill"))
                }

                if pillEnabled {
                    Divider()

                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("Style")
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(JiSprTheme.ink)
                            Text("Compact stays subtle; expanded shows more status detail.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Picker("Style", selection: store.stringBinding("pill_style")) {
                            Text("Compact").tag("compact")
                            Text("Expanded").tag("expanded")
                        }
                        .labelsHidden()
                        .pickerStyle(.segmented)
                        .frame(width: 240)
                        .disabled(!store.isEditable("pill_style"))
                    }
                }
            }

            SectionCard(
                "Live preview",
                subtitle: pillEnabled
                    ? "Switch styles above to compare them instantly"
                    : "Turn on the recording pill to preview it"
            ) {
                ZStack {
                    RoundedRectangle(cornerRadius: 22)
                        .fill(JiSprTheme.canvas)
                    RoundedRectangle(cornerRadius: 22)
                        .stroke(JiSprTheme.border.opacity(0.8), lineWidth: 1)

                    if pillEnabled {
                        FloatingPillView(configuration: previewConfiguration)
                            .shadow(color: .black.opacity(0.16), radius: 12, y: 5)
                            .transition(.scale(scale: 0.96).combined(with: .opacity))
                    } else {
                        VStack(spacing: 10) {
                            Image(systemName: "eye.slash")
                                .font(.system(size: 28, weight: .medium))
                                .foregroundStyle(JiSprTheme.orange)
                            Text("Recording pill is hidden")
                                .font(.headline)
                                .foregroundStyle(JiSprTheme.ink)
                            Text("Your menu-bar icon will still show JiSpr's status.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .frame(height: 190)
                .animation(.easeInOut(duration: 0.18), value: pillEnabled)
                .animation(.easeInOut(duration: 0.18), value: pillStyle)

                HStack(spacing: 18) {
                    Label("Bottom center", systemImage: "macwindow")
                    Label("Sage when ready", systemImage: "circle.fill")
                        .foregroundStyle(JiSprTheme.sage)
                    Label("Orange when active", systemImage: "circle.fill")
                        .foregroundStyle(JiSprTheme.orange)
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
    }

    private var previewConfiguration: FloatingPillConfiguration {
        FloatingPillConfiguration(
            enabled: true,
            style: pillStyle,
            state: .recording,
            detail: "",
            audioLevel: 0.68,
            hotkey: store.stringValue("hotkey").isEmpty
                ? "fn"
                : store.stringValue("hotkey")
        )
    }
}
