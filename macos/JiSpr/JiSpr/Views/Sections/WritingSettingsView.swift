import SwiftUI

struct WritingSettingsView: View {
    let store: AppStore

    var body: some View {
        DetailPage("Writing", subtitle: "Shape the text JiSpr inserts for you.") {
            SectionCard("Polish and Style") {
                pickerRow("Cleanup level", key: "cleanup_level", options: store.options("cleanup_level"))
                pickerRow("Writing style", key: "style", options: store.snapshot?.styles ?? [])
                SettingRow(
                    "Additional prompt",
                    help: "Extra instructions appended to JiSpr's protected local prompt.",
                    source: store.source("lmstudio_system_prompt"),
                    editable: store.isEditable("lmstudio_system_prompt")
                ) {
                    TextEditor(text: store.stringBinding("lmstudio_system_prompt"))
                        .font(.body)
                        .frame(minHeight: 88)
                        .padding(5)
                        .background(.background, in: RoundedRectangle(cornerRadius: 7))
                        .overlay {
                            RoundedRectangle(cornerRadius: 7)
                                .stroke(.separator, lineWidth: 1)
                        }
                }
            }

            SectionCard("Context and Insertion") {
                toggleRow(
                    "Per-app style",
                    help: "Use app-specific writing and insertion rules.",
                    key: "context_styles"
                )
                toggleRow(
                    "Focused-field context",
                    help: "Read nearby text locally so new writing continues naturally.",
                    key: "context_awareness"
                )
                pickerRow("Insert method", key: "insert_method", options: store.options("insert_method"))
                pickerRow("Streaming", key: "streaming", options: store.options("streaming"))
            }

            SectionCard("Transforms", subtitle: "Optional rewrites after dictation") {
                pickerRow(
                    "Default transform",
                    key: "transform_default",
                    options: store.snapshot?.transforms ?? []
                )
                pickerRow(
                    "Auto-transform",
                    key: "auto_transform",
                    options: [""] + (store.snapshot?.transforms ?? []),
                    emptyLabel: "Disabled"
                )
                SettingRow(
                    "Transform hotkey",
                    source: store.source("transform_hotkey"),
                    editable: store.isEditable("transform_hotkey")
                ) {
                    TextField("Disabled", text: store.stringBinding("transform_hotkey"))
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 220)
                }
                SettingRow(
                    "Voice command hotkey",
                    source: store.source("command_hotkey"),
                    editable: store.isEditable("command_hotkey")
                ) {
                    TextField("Disabled", text: store.stringBinding("command_hotkey"))
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 220)
                }
            }
        }
    }

    @ViewBuilder
    private func pickerRow(
        _ title: String,
        key: String,
        options: [String],
        emptyLabel: String = ""
    ) -> some View {
        SettingRow(title, source: store.source(key), editable: store.isEditable(key)) {
            Picker("", selection: store.stringBinding(key)) {
                ForEach(options, id: \.self) { value in
                    Text(value.isEmpty ? emptyLabel : value).tag(value)
                }
            }
            .labelsHidden()
            .frame(width: 240)
        }
    }

    @ViewBuilder
    private func toggleRow(_ title: String, help: String, key: String) -> some View {
        SettingRow(
            title,
            help: help,
            source: store.source(key),
            editable: store.isEditable(key)
        ) {
            Toggle("", isOn: store.boolBinding(key))
                .labelsHidden()
                .toggleStyle(.switch)
        }
    }
}
