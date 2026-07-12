import SwiftUI

struct PersonalizationSettingsView: View {
    let store: AppStore
    @State private var search = ""
    @State private var selectedTerm: String?
    @State private var term = ""
    @State private var starred = false
    @State private var selectedAlias: String?
    @State private var trigger = ""
    @State private var expansion = ""

    private var filteredDictionary: [DictionaryEntry] {
        let entries = store.snapshot?.dictionary ?? []
        guard !search.isEmpty else { return entries }
        return entries.filter { $0.term.localizedCaseInsensitiveContains(search) }
    }

    var body: some View {
        DetailPage("Personalization", subtitle: "Teach JiSpr the words and corrections that matter to you.") {
            SectionCard("Dictionary", subtitle: "Canonical spellings are enforced locally") {
                HStack(alignment: .top, spacing: 16) {
                    VStack(spacing: 8) {
                        TextField("Search words", text: $search)
                            .textFieldStyle(.roundedBorder)
                        List(filteredDictionary, selection: $selectedTerm) { entry in
                            HStack {
                                Image(systemName: entry.starred == true ? "star.fill" : "textformat")
                                    .foregroundStyle(entry.starred == true ? JiSprTheme.orange : .secondary)
                                Text(entry.term)
                                Spacer()
                                if let uses = entry.uses, uses > 0 {
                                    Text("\(uses) uses")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .tag(entry.term)
                        }
                        .frame(minHeight: 190)
                        .onChange(of: selectedTerm) { _, value in
                            guard let value,
                                  let entry = store.snapshot?.dictionary.first(where: { $0.term == value })
                            else { return }
                            term = entry.term
                            starred = entry.starred ?? false
                        }
                    }
                    .frame(minWidth: 300)

                    VStack(alignment: .leading, spacing: 12) {
                        TextField("Word or phrase", text: $term)
                            .textFieldStyle(.roundedBorder)
                        Toggle("Prioritize this spelling", isOn: $starred)
                            .toggleStyle(.switch)
                        Spacer()
                        HStack {
                            Button("Add") {
                                store.addDictionary(term: term)
                                term = ""
                                starred = false
                            }
                            .disabled(term.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                            Button("Update") {
                                guard let selectedTerm else { return }
                                store.updateDictionary(
                                    original: selectedTerm,
                                    term: term,
                                    starred: starred
                                )
                            }
                            .disabled(selectedTerm == nil || term.isEmpty)
                            Button("Remove", role: .destructive) {
                                guard let selectedTerm else { return }
                                store.removeDictionary(term: selectedTerm)
                                self.selectedTerm = nil
                                term = ""
                            }
                            .disabled(selectedTerm == nil)
                        }
                    }
                    .frame(width: 260)
                }
            }

            SectionCard("Correction Aliases", subtitle: "Replace a recurring mishearing with the intended text") {
                HStack(alignment: .top, spacing: 16) {
                    List(selection: $selectedAlias) {
                        ForEach((store.snapshot?.aliases.keys.sorted() ?? []), id: \.self) { key in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(key)
                                Text(store.snapshot?.aliases[key] ?? "")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            .tag(key)
                        }
                    }
                    .frame(minWidth: 300, minHeight: 170)
                    .onChange(of: selectedAlias) { _, value in
                        guard let value else { return }
                        trigger = value
                        expansion = store.snapshot?.aliases[value] ?? ""
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        TextField("What JiSpr hears", text: $trigger)
                            .textFieldStyle(.roundedBorder)
                        TextField("What JiSpr should write", text: $expansion)
                            .textFieldStyle(.roundedBorder)
                        Spacer()
                        HStack {
                            Button("Add") {
                                store.addAlias(trigger: trigger, expansion: expansion)
                                trigger = ""
                                expansion = ""
                            }
                            .disabled(trigger.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                            Button("Update") {
                                guard let selectedAlias else { return }
                                store.updateAlias(
                                    original: selectedAlias,
                                    trigger: trigger,
                                    expansion: expansion
                                )
                            }
                            .disabled(selectedAlias == nil || trigger.isEmpty)
                            Button("Remove", role: .destructive) {
                                guard let selectedAlias else { return }
                                store.removeAlias(trigger: selectedAlias)
                                self.selectedAlias = nil
                                trigger = ""
                                expansion = ""
                            }
                            .disabled(selectedAlias == nil)
                        }
                    }
                    .frame(width: 260)
                }
            }
        }
    }
}
