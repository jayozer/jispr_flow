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
        AppStore.shared.requestPrimaryWindow()
    }

    func applicationShouldHandleReopen(
        _ sender: NSApplication,
        hasVisibleWindows flag: Bool
    ) -> Bool {
        AppStore.shared.requestPrimaryWindow()
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

        Window("Welcome to JiSpr", id: "guided-setup") {
            GuidedSetupWindow(store: store)
        }
        .defaultSize(width: 860, height: 740)
        .windowResizability(.contentMinSize)

        Window("Teach Me Once", id: "teach-me-once") {
            TeachMeOnceView(model: store.teachMeOnce)
        }
        .defaultSize(width: 800, height: 700)
        .windowResizability(.contentMinSize)

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
                guard !isRunningTests else { return }
                if store.shouldPresentGuidedSetup {
                    openSetup()
                } else if store.settingsRequestID > 0
                    || !UserDefaults.standard.bool(forKey: "hasOpenedJiSprSettings") {
                    UserDefaults.standard.set(true, forKey: "hasOpenedJiSprSettings")
                    openSettings()
                }
            }
            .onChange(of: store.settingsRequestID) {
                openSettings()
            }
            .onChange(of: store.setupRequestID) { openSetup() }
    }

    private func openSettings() {
        UserDefaults.standard.set(true, forKey: "hasOpenedJiSprSettings")
        openWindow(id: "settings")
        NSApp.activate(ignoringOtherApps: true)
    }

    private func openSetup() {
        openWindow(id: "guided-setup")
        NSApp.activate(ignoringOtherApps: true)
    }
}

private struct GuidedSetupWindow: View {
    let store: AppStore
    @Environment(\.dismissWindow) private var dismissWindow

    var body: some View {
        SetupView(model: store.setup)
            .onChange(of: store.setupDismissID) { dismissWindow(id: "guided-setup") }
            .onDisappear { store.setupWindowClosed() }
    }
}
