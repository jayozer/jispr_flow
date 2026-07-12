import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var handledInitialActivation = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        AppStore.shared.launch()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationWillTerminate(_ notification: Notification) {
        AppStore.shared.applicationWillTerminate()
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        AppStore.shared.refreshPermissions()
        guard !handledInitialActivation else { return }
        handledInitialActivation = true
        AppStore.shared.requestSettingsWindow()
    }

    func applicationShouldHandleReopen(
        _ sender: NSApplication,
        hasVisibleWindows flag: Bool
    ) -> Bool {
        AppStore.shared.requestSettingsWindow()
        return true
    }
}

@main
struct JiSprApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var store = AppStore.shared

    var body: some Scene {
        MenuBarExtra {
            MenuBarContent(store: store)
        } label: {
            MenuBarLabel(store: store)
        }
        .menuBarExtraStyle(.menu)

        Window("JiSpr Settings", id: "settings") {
            SettingsRootView(store: store)
        }
        .defaultSize(width: 940, height: 700)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .appTermination) {
                Button("Quit JiSpr") { store.quit() }
                    .keyboardShortcut("q")
            }
        }
    }
}

private struct MenuBarLabel: View {
    let store: AppStore
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Label("JiSpr", systemImage: store.menuSymbol)
            .help(store.statusTitle)
            .task {
                let isRunningTests = ProcessInfo.processInfo.environment[
                    "XCTestConfigurationFilePath"
                ] != nil
                if !isRunningTests,
                   store.settingsRequestID > 0
                    || !UserDefaults.standard.bool(forKey: "hasOpenedJiSprSettings") {
                    UserDefaults.standard.set(true, forKey: "hasOpenedJiSprSettings")
                    openSettings()
                }
            }
            .onChange(of: store.settingsRequestID) {
                openSettings()
            }
    }

    private func openSettings() {
        UserDefaults.standard.set(true, forKey: "hasOpenedJiSprSettings")
        openWindow(id: "settings")
        NSApp.activate(ignoringOtherApps: true)
    }
}
