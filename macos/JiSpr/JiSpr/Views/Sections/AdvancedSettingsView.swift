import SwiftUI

struct AdvancedSettingsView: View {
    let store: AppStore

    var body: some View {
        DetailPage("Advanced", subtitle: "Tune detection, resilience, and specialist shortcuts.") {
            SectionCard("Voice Activity Detection") {
                pickerRow("Backend", key: "vad_backend")
                pickerRow("Quiet voice preset", key: "vad_preset")
                integerRow("Aggressiveness", key: "vad_aggressiveness")
                integerRow("Frame length (ms)", key: "vad_frame_ms")
                integerRow("Silence cutoff (ms)", key: "vad_silence_ms")
                doubleRow("Energy threshold", key: "vad_energy_threshold")
            }

            SectionCard("Reliability") {
                SettingRow(
                    "Crash-safe audio recovery",
                    help: "Temporarily saves captured audio until insertion succeeds.",
                    source: store.source("audio_recovery"),
                    editable: store.isEditable("audio_recovery")
                ) {
                    Toggle("", isOn: store.boolBinding("audio_recovery"))
                        .labelsHidden()
                        .toggleStyle(.switch)
                }
                integerRow("Long recording warning (min)", key: "max_utterance_min")
                integerRow("Maximum history entries", key: "history_max_entries")
                integerRow("Streaming pause (ms)", key: "streaming_pause_ms")
            }

            SectionCard("Specialist Hotkeys") {
                textRow("Scratchpad hotkey", key: "scratchpad_hotkey", placeholder: "Disabled")
                textRow("Mouse Enter button", key: "mouse_enter_button", placeholder: "Disabled")
                integerRow("Space hold threshold (ms)", key: "hotkey_space_hold_ms")
            }
        }
    }

    @ViewBuilder
    private func pickerRow(_ title: String, key: String) -> some View {
        SettingRow(title, source: store.source(key), editable: store.isEditable(key)) {
            Picker("", selection: store.stringBinding(key)) {
                ForEach(store.options(key), id: \.self) { Text($0).tag($0) }
            }
            .labelsHidden()
            .frame(width: 220)
        }
    }

    @ViewBuilder
    private func integerRow(_ title: String, key: String) -> some View {
        SettingRow(title, source: store.source(key), editable: store.isEditable(key)) {
            TextField("", value: store.intBinding(key), format: .number)
                .textFieldStyle(.roundedBorder)
                .frame(width: 130)
        }
    }

    @ViewBuilder
    private func doubleRow(_ title: String, key: String) -> some View {
        SettingRow(title, source: store.source(key), editable: store.isEditable(key)) {
            TextField("", value: store.doubleBinding(key), format: .number)
                .textFieldStyle(.roundedBorder)
                .frame(width: 130)
        }
    }

    @ViewBuilder
    private func textRow(_ title: String, key: String, placeholder: String) -> some View {
        SettingRow(title, source: store.source(key), editable: store.isEditable(key)) {
            TextField(placeholder, text: store.stringBinding(key))
                .textFieldStyle(.roundedBorder)
                .frame(width: 220)
        }
    }
}
