import SwiftUI

struct ModelSettingsView: View {
    let store: AppStore

    var body: some View {
        DetailPage("Models", subtitle: "Local speech recognition and writing polish.") {
            SectionCard("Speech Recognition", subtitle: "Audio never leaves this Mac") {
                pickerRow("Preset", key: "asr_profile")
                pickerRow("Backend", key: "asr_backend", marksCustom: true)

                SettingRow(
                    "Model",
                    help: "Hugging Face identifier or a local model directory.",
                    source: store.source("asr_model"),
                    editable: store.isEditable("asr_model")
                ) {
                    TextField("Model identifier", text: customModelBinding)
                        .textFieldStyle(.roundedBorder)
                        .frame(minWidth: 260)
                }

                SettingRow(
                    "Language",
                    help: "Use auto to detect each utterance.",
                    source: store.source("asr_language"),
                    editable: store.isEditable("asr_language")
                ) {
                    TextField("auto", text: store.stringBinding("asr_language"))
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 220)
                }

                pickerRow("Device", key: "asr_device")
                pickerRow("Compute type", key: "asr_compute_type")
            }

            SectionCard("Writing Polish", subtitle: "LM Studio stays on localhost") {
                pickerRow("Backend", key: "polish_backend")
                SettingRow(
                    "LM Studio model",
                    help: "Leave blank to use the first loaded model.",
                    source: store.source("lmstudio_model"),
                    editable: store.isEditable("lmstudio_model")
                ) {
                    HStack {
                        if store.loadedModels.isEmpty {
                            TextField("Auto-select", text: store.stringBinding("lmstudio_model"))
                                .textFieldStyle(.roundedBorder)
                        } else {
                            Picker("", selection: store.stringBinding("lmstudio_model")) {
                                Text("Auto-select").tag("")
                                ForEach(store.loadedModels, id: \.self) { model in
                                    Text(isS1Mini(model) ? "S1-mini by Superwhisper (\(model))" : model)
                                        .tag(model)
                                }
                            }
                            .labelsHidden()
                        }
                        Button("Refresh") { store.refreshModels() }
                    }
                    .frame(minWidth: 300)
                }
                Text(store.modelStatus)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .trailing)
                if isS1Mini(selectedPolishModel) {
                    Text("S1-mini by Superwhisper cleans up English dictation. "
                         + "JiSpr configures it automatically. Custom instructions and field "
                         + "context are unsupported; command and snippet utterances use rules. "
                         + "Select Gemma or another general-purpose model for rewriting.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var selectedPolishModel: String {
        let model = store.stringValue("lmstudio_model")
        return model.isEmpty ? (store.loadedModels.first ?? "") : model
    }

    private func isS1Mini(_ model: String) -> Bool {
        model.range(of: #"(?i)(?:^|[/\\])s1-mini(?:$|[-_.:@/])"#,
                    options: .regularExpression) != nil
    }

    private var customModelBinding: Binding<String> {
        Binding(
            get: { store.stringValue("asr_model") },
            set: {
                store.setDraft(.string($0), for: "asr_model")
                store.setDraft(.string("custom"), for: "asr_profile")
            }
        )
    }

    @ViewBuilder
    private func pickerRow(_ title: String, key: String, marksCustom: Bool = false) -> some View {
        SettingRow(
            title,
            source: store.source(key),
            editable: store.isEditable(key)
        ) {
            Picker("", selection: pickerBinding(key, marksCustom: marksCustom)) {
                ForEach(store.options(key), id: \.self) { Text($0).tag($0) }
            }
            .labelsHidden()
            .frame(width: 240)
        }
    }

    private func pickerBinding(_ key: String, marksCustom: Bool) -> Binding<String> {
        Binding(
            get: { store.stringValue(key) },
            set: {
                store.setDraft(.string($0), for: key)
                if marksCustom { store.setDraft(.string("custom"), for: "asr_profile") }
            }
        )
    }
}
