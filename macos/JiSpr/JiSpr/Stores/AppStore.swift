import AppKit
import ApplicationServices
import CoreGraphics
import Foundation
import Observation
import ServiceManagement
import SwiftUI

@MainActor
@Observable
final class AppStore {
    static let shared = AppStore()

    private enum PendingAction {
        case start
        case stop
        case reload
        case save(Set<String>)
        case refreshModels
        case mutation
    }

    private let engine = EngineProcessService()
    private var pendingActions: [String: PendingAction] = [:]
    private(set) var drafts: [String: JSONValue] = [:]
    private(set) var dirtyFields: Set<String> = []

    private(set) var snapshot: HostSnapshot?
    private(set) var state: DictationState = .offline
    private(set) var stateDetail = "Starting local engine…"
    private(set) var audioLevel = 0.0
    private(set) var isHostOnline = false
    private(set) var dictationEnabled = false
    private(set) var pendingRestart = false
    private(set) var bannerMessage: String?
    private(set) var modelStatus = "Not checked"
    private(set) var loadedModels: [String] = []
    private(set) var launchAtLogin = false
    private(set) var accessibilityGranted = AXIsProcessTrusted()
    private(set) var inputMonitoringGranted = CGPreflightListenEventAccess()
    private var isQuitting = false

    private init() {
        launchAtLogin = SMAppService.mainApp.status == .enabled
    }

    var menuSymbol: String { state.symbol }
    var statusTitle: String { state.title }
    var needsPermissionAttention: Bool {
        state == .warning && stateDetail.localizedCaseInsensitiveContains("permission")
    }
    var hasUnsavedChanges: Bool { !dirtyFields.isEmpty }

    var availableLanguages: [String] {
        var values = ["auto", "en", "es", "fr", "de"]
        let configured = stringValue("languages")
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        values.append(contentsOf: configured)
        if !stringValue("asr_language").isEmpty {
            values.append(stringValue("asr_language"))
        }
        return values.reduce(into: []) { result, value in
            if !result.contains(value) { result.append(value) }
        }
    }

    func launch() {
        guard !engine.isRunning else { return }
        do {
            let location = try EngineLocator.resolve()
            state = .offline
            stateDetail = "Starting local engine…"
            try engine.start(
                location: location,
                onMessage: { message in
                    Task { @MainActor in AppStore.shared.receive(message) }
                },
                onDiagnostic: { diagnostic in
                    Task { @MainActor in AppStore.shared.receiveDiagnostic(diagnostic) }
                },
                onTermination: { status in
                    Task { @MainActor in AppStore.shared.engineTerminated(status: status) }
                }
            )
        } catch {
            state = .error
            stateDetail = error.localizedDescription
            bannerMessage = error.localizedDescription
        }
    }

    func toggleDictation() {
        if dictationEnabled {
            let id = engine.send(command: "stop")
            pendingActions[id] = .stop
        } else {
            let id = engine.send(command: "start")
            pendingActions[id] = .start
        }
    }

    func restartEngine() {
        bannerMessage = nil
        if isHostOnline {
            let id = engine.send(command: "reload", payload: ["start": true])
            pendingActions[id] = .reload
        } else {
            launch()
        }
    }

    func quit() {
        isQuitting = true
        engine.stop()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
            NSApplication.shared.terminate(nil)
        }
    }

    func applicationWillTerminate() {
        isQuitting = true
        engine.stop(forceAfter: 0.2)
    }

    func openDataFolder() {
        guard let path = snapshot?.dataDir else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path, isDirectory: true))
    }

    func setLaunchAtLogin(_ enabled: Bool) {
        do {
            if enabled {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
            launchAtLogin = SMAppService.mainApp.status == .enabled
            bannerMessage = nil
        } catch {
            launchAtLogin = SMAppService.mainApp.status == .enabled
            bannerMessage = "Launch at Login could not be changed: \(error.localizedDescription)"
        }
    }

    func refreshPermissions() {
        accessibilityGranted = AXIsProcessTrusted()
        inputMonitoringGranted = CGPreflightListenEventAccess()
        if accessibilityGranted, inputMonitoringGranted,
           bannerMessage?.contains("Fn hotkey is paused") == true {
            bannerMessage = "Permissions granted—restart the JiSpr engine to enable Fn."
        }
    }

    func requestAccessibilityPermission() {
        let options = [
            "AXTrustedCheckOptionPrompt": true,
        ] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
        schedulePermissionRefresh()
    }

    func requestInputMonitoringPermission() {
        _ = CGRequestListenEventAccess()
        schedulePermissionRefresh()
    }

    func openAccessibilitySettings() {
        openPrivacySettings(anchor: "Privacy_Accessibility")
    }

    func openInputMonitoringSettings() {
        openPrivacySettings(anchor: "Privacy_ListenEvent")
    }

    func setDraft(_ value: JSONValue, for key: String) {
        guard isEditable(key) else { return }
        drafts[key] = value
        if snapshot?.settings[key]?.value == value {
            dirtyFields.remove(key)
        } else {
            dirtyFields.insert(key)
        }
    }

    func saveSettings() {
        guard !dirtyFields.isEmpty else { return }
        var changes: [String: Any] = [:]
        for key in dirtyFields {
            if let value = drafts[key] {
                changes[key] = value.foundationValue
            }
        }
        let savedFields = dirtyFields
        let id = engine.send(command: "save_settings", payload: ["changes": changes])
        pendingActions[id] = .save(savedFields)
        bannerMessage = "Saving…"
    }

    func discardChanges() {
        guard let snapshot else { return }
        for key in dirtyFields {
            drafts[key] = snapshot.settings[key]?.value
        }
        dirtyFields.removeAll()
        bannerMessage = nil
    }

    func setStyle(_ name: String) {
        setDraft(.string(name), for: "style")
        let id = engine.send(command: "set_style", payload: ["name": name])
        pendingActions[id] = .mutation
        dirtyFields.remove("style")
    }

    func setLanguage(_ code: String) {
        setDraft(.string(code), for: "asr_language")
        let id = engine.send(command: "set_language", payload: ["code": code])
        pendingActions[id] = .mutation
        dirtyFields.remove("asr_language")
    }

    func refreshModels() {
        modelStatus = "Checking LM Studio…"
        let id = engine.send(command: "refresh_models")
        pendingActions[id] = .refreshModels
    }

    func addDictionary(term: String) {
        mutate("dictionary_add", payload: ["term": term])
    }

    func updateDictionary(original: String, term: String, starred: Bool) {
        mutate(
            "dictionary_update",
            payload: ["original": original, "term": term, "starred": starred]
        )
    }

    func removeDictionary(term: String) {
        mutate("dictionary_remove", payload: ["term": term])
    }

    func addAlias(trigger: String, expansion: String) {
        mutate("alias_add", payload: ["trigger": trigger, "expansion": expansion])
    }

    func updateAlias(original: String, trigger: String, expansion: String) {
        mutate(
            "alias_update",
            payload: ["original": original, "trigger": trigger, "expansion": expansion]
        )
    }

    func removeAlias(trigger: String) {
        mutate("alias_remove", payload: ["trigger": trigger])
    }

    func stringValue(_ key: String) -> String {
        drafts[key]?.stringValue ?? snapshot?.settings[key]?.value.stringValue ?? ""
    }

    func boolValue(_ key: String) -> Bool {
        drafts[key]?.boolValue ?? snapshot?.settings[key]?.value.boolValue ?? false
    }

    func intValue(_ key: String) -> Int {
        drafts[key]?.intValue ?? snapshot?.settings[key]?.value.intValue ?? 0
    }

    func doubleValue(_ key: String) -> Double {
        drafts[key]?.doubleValue ?? snapshot?.settings[key]?.value.doubleValue ?? 0
    }

    func isEditable(_ key: String) -> Bool {
        snapshot?.settings[key]?.editable ?? false
    }

    func source(_ key: String) -> String {
        snapshot?.settings[key]?.source ?? "loading"
    }

    func options(_ key: String) -> [String] {
        snapshot?.options[key] ?? []
    }

    func stringBinding(_ key: String) -> Binding<String> {
        Binding(
            get: { self.stringValue(key) },
            set: { self.setDraft(.string($0), for: key) }
        )
    }

    func boolBinding(_ key: String) -> Binding<Bool> {
        Binding(
            get: { self.boolValue(key) },
            set: { self.setDraft(.bool($0), for: key) }
        )
    }

    func intBinding(_ key: String) -> Binding<Int> {
        Binding(
            get: { self.intValue(key) },
            set: { self.setDraft(.int($0), for: key) }
        )
    }

    func doubleBinding(_ key: String) -> Binding<Double> {
        Binding(
            get: { self.doubleValue(key) },
            set: { self.setDraft(.double($0), for: key) }
        )
    }

    private func mutate(_ command: String, payload: [String: Any]) {
        let id = engine.send(command: command, payload: payload)
        pendingActions[id] = .mutation
    }

    private func receive(_ message: HostMessage) {
        switch message.event {
        case "ready":
            isHostOnline = true
            bannerMessage = nil
            let id = engine.send(command: "start")
            pendingActions[id] = .start
        case "snapshot":
            if let snapshot = message.snapshot { apply(snapshot) }
        case "state":
            if let rawState = message.state {
                state = DictationState(hostValue: rawState)
                stateDetail = message.detail?.isEmpty == false ? message.detail! : state.title
                if pendingRestart, !state.isBusy {
                    pendingRestart = false
                    let id = engine.send(command: "reload", payload: ["start": true])
                    pendingActions[id] = .reload
                    bannerMessage = "Applying saved settings…"
                }
            }
        case "audio_level":
            audioLevel = message.level ?? 0
        case "reply":
            receiveReply(message)
        case "error":
            let text = [message.message, message.hint].compactMap { $0 }.joined(separator: " ")
            bannerMessage = text
            if message.id == nil {
                state = .error
                stateDetail = text
            }
            if let id = message.id { pendingActions[id] = nil }
        default:
            break
        }
    }

    private func receiveReply(_ message: HostMessage) {
        guard let id = message.id, let action = pendingActions.removeValue(forKey: id) else {
            return
        }
        switch action {
        case .start:
            dictationEnabled = true
            state = .idle
            stateDetail = "Ready for your hotkey"
        case .stop:
            dictationEnabled = false
            state = .offline
            stateDetail = "Dictation paused"
        case .reload:
            dictationEnabled = true
            bannerMessage = "Settings applied"
        case let .save(fields):
            dirtyFields.subtract(fields)
            let requiresRestart = message.result?["requires_restart"]?.boolValue ?? false
            if requiresRestart, state.isBusy {
                pendingRestart = true
                bannerMessage = "Saved—applying after this dictation"
            } else if requiresRestart {
                let reloadID = engine.send(command: "reload", payload: ["start": true])
                pendingActions[reloadID] = .reload
                bannerMessage = "Saved—restarting the local engine…"
            } else {
                bannerMessage = "Saved"
            }
        case .refreshModels:
            if case let .array(values)? = message.result?["models"] {
                loadedModels = values.compactMap(\.stringValue)
            }
            modelStatus = message.result?["status"]?.stringValue ?? "Model check complete"
        case .mutation:
            bannerMessage = nil
        }
    }

    private func apply(_ snapshot: HostSnapshot) {
        self.snapshot = snapshot
        for (key, setting) in snapshot.settings where !dirtyFields.contains(key) {
            drafts[key] = setting.value
        }
    }

    private func receiveDiagnostic(_ diagnostic: String) {
        guard !diagnostic.isEmpty else { return }
        if diagnostic.contains("Could not create the macOS event tap") {
            state = .warning
            stateDetail = "Fn hotkey needs permission"
            bannerMessage = "Fn hotkey is paused. Open General → Permissions, grant JiSpr access, then restart the engine."
            refreshPermissions()
        } else {
            bannerMessage = diagnostic
        }
    }

    private func engineTerminated(status: Int32) {
        isHostOnline = false
        dictationEnabled = false
        pendingActions.removeAll()
        guard !isQuitting else { return }
        state = .error
        stateDetail = "Local engine exited with status \(status)"
        bannerMessage = "The local engine stopped. Choose Restart Engine from the JiSpr menu."
    }

    private func schedulePermissionRefresh() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
            self?.refreshPermissions()
        }
    }

    private func openPrivacySettings(anchor: String) {
        guard let url = URL(
            string: "x-apple.systempreferences:com.apple.preference.security?\(anchor)"
        ) else { return }
        NSWorkspace.shared.open(url)
    }
}
