import AppKit
import SwiftUI

struct MenuBarContent: View {
    let store: AppStore
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button(store.statusTitle) {}
            .disabled(true)

        if !store.stateDetail.isEmpty, store.stateDetail != store.statusTitle {
            Button(store.stateDetail) {}
                .disabled(true)
        }

        if store.needsPermissionAttention {
            Button {
                openWindow(id: "settings")
                NSApp.activate(ignoringOtherApps: true)
            } label: {
                Label("Fix Fn Permission…", systemImage: "lock.shield")
            }
        }

        Button {
            store.toggleDictation()
        } label: {
            Label(
                store.dictationEnabled ? "Pause Dictation" : "Start Dictation",
                systemImage: store.dictationEnabled ? "pause.circle" : "play.circle"
            )
        }
        .disabled(!store.isHostOnline)

        Divider()

        if let snapshot = store.snapshot, !snapshot.styles.isEmpty {
            Menu("Writing Style") {
                ForEach(snapshot.styles, id: \.self) { style in
                    Button {
                        store.setStyle(style)
                    } label: {
                        if style == store.stringValue("style") {
                            Label(shortTitle(style), systemImage: "checkmark")
                        } else {
                            Text(shortTitle(style))
                        }
                    }
                }
            }
        }

        Menu("Language") {
            ForEach(store.availableLanguages, id: \.self) { language in
                Button {
                    store.setLanguage(language)
                } label: {
                    if language == store.stringValue("asr_language") {
                        Label(language, systemImage: "checkmark")
                    } else {
                        Text(language)
                    }
                }
            }
        }

        Button {
            openWindow(id: "settings")
            NSApp.activate(ignoringOtherApps: true)
        } label: {
            Label("Open Settings", systemImage: "gearshape")
        }

        Button {
            store.openDataFolder()
        } label: {
            Label("Open Data Folder", systemImage: "folder")
        }
        .disabled(store.snapshot == nil)

        Toggle(
            "Launch at Login",
            isOn: Binding(
                get: { store.launchAtLogin },
                set: { store.setLaunchAtLogin($0) }
            )
        )

        Divider()

        Button {
            store.restartEngine()
        } label: {
            Label("Restart Engine", systemImage: "arrow.clockwise")
        }

        Button("Quit JiSpr") { store.quit() }
            .keyboardShortcut("q")
    }

    private func shortTitle(_ value: String) -> String {
        guard value.count > 30 else { return value }
        return String(value.prefix(27)) + "…"
    }
}
