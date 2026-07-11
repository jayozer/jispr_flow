import SwiftUI

struct GeneralSettingsView: View {
    let store: AppStore

    var body: some View {
        DetailPage("General", subtitle: "Choose how JiSpr listens and starts.") {
            StatusHero(store: store)

            SectionCard(
                "Permissions",
                subtitle: "Required for the Fn hotkey and inserting text into other apps"
            ) {
                PermissionRow(
                    title: "Accessibility",
                    detail: "Lets JiSpr insert text and observe the global Fn shortcut.",
                    granted: store.accessibilityGranted,
                    request: store.requestAccessibilityPermission,
                    openSettings: store.openAccessibilitySettings
                )
                Divider()
                PermissionRow(
                    title: "Input Monitoring",
                    detail: "Click Request Access once so JiSpr appears in macOS Input Monitoring.",
                    granted: store.inputMonitoringGranted,
                    request: store.requestInputMonitoringPermission,
                    openSettings: store.openInputMonitoringSettings
                )
            }

            SectionCard("Dictation", subtitle: "Your everyday capture controls") {
                SettingRow(
                    "Mode",
                    help: "Push-to-talk listens only while the hotkey is held.",
                    source: store.source("mode"),
                    editable: store.isEditable("mode")
                ) {
                    Picker("", selection: store.stringBinding("mode")) {
                        ForEach(store.options("mode"), id: \.self) { Text($0).tag($0) }
                    }
                    .labelsHidden()
                    .frame(width: 220)
                }

                Divider()

                SettingRow(
                    "Dictation hotkey",
                    help: "Examples: fn, space, or f9.",
                    source: store.source("hotkey"),
                    editable: store.isEditable("hotkey")
                ) {
                    TextField("fn", text: store.stringBinding("hotkey"))
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 220)
                }

                SettingRow(
                    "Cancel hotkey",
                    help: "Discards the recording currently in progress.",
                    source: store.source("cancel_hotkey"),
                    editable: store.isEditable("cancel_hotkey")
                ) {
                    TextField("esc", text: store.stringBinding("cancel_hotkey"))
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 220)
                }

                SettingRow(
                    "Microphone priority",
                    help: "Comma-separated device names, first choice first.",
                    source: store.source("mic_priority"),
                    editable: store.isEditable("mic_priority")
                ) {
                    TextField("AirPods, USB", text: store.stringBinding("mic_priority"))
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 260)
                }
            }

            SectionCard("Mouse and Startup") {
                SettingRow(
                    "Mouse dictation",
                    help: "Optional non-primary mouse button.",
                    source: store.source("mouse_button"),
                    editable: store.isEditable("mouse_button")
                ) {
                    Picker("", selection: store.stringBinding("mouse_button")) {
                        Text("Disabled").tag("")
                        ForEach(store.options("mouse_button").filter { !$0.isEmpty }, id: \.self) {
                            Text($0).tag($0)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 220)
                }

                SettingRow(
                    "Launch at Login",
                    help: "Keep JiSpr available in the menu bar after signing in.",
                    source: "macOS",
                    editable: true
                ) {
                    Toggle(
                        "",
                        isOn: Binding(
                            get: { store.launchAtLogin },
                            set: { store.setLaunchAtLogin($0) }
                        )
                    )
                    .labelsHidden()
                    .toggleStyle(.switch)
                }
            }

            SectionCard("Local History", subtitle: "Stored only on this Mac") {
                SettingRow(
                    "Save history",
                    source: store.source("history_enabled"),
                    editable: store.isEditable("history_enabled")
                ) {
                    Toggle("", isOn: store.boolBinding("history_enabled"))
                        .labelsHidden()
                        .toggleStyle(.switch)
                }
                SettingRow(
                    "Retention",
                    source: store.source("history_retention"),
                    editable: store.isEditable("history_retention")
                ) {
                    Picker("", selection: store.stringBinding("history_retention")) {
                        ForEach(store.options("history_retention"), id: \.self) { Text($0).tag($0) }
                    }
                    .labelsHidden()
                    .frame(width: 220)
                }
            }
        }
    }
}

private struct PermissionRow: View {
    let title: String
    let detail: String
    let granted: Bool
    let request: () -> Void
    let openSettings: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: granted ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                .font(.title3)
                .foregroundStyle(granted ? JiSprTheme.sage : JiSprTheme.orange)
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(JiSprTheme.ink)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if granted {
                Text("Granted")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(JiSprTheme.sage)
            } else {
                Button("Open Settings", action: openSettings)
                Button("Request Access", action: request)
                    .buttonStyle(.borderedProminent)
                    .tint(JiSprTheme.orange)
            }
        }
    }
}
