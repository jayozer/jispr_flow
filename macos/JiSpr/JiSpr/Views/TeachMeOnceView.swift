import AppKit
import SwiftUI

struct TeachMeOnceView: View {
    let model: TeachMeOnceStore
    @State private var showsOriginal = false
    @State private var copied = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header
                if let error = model.errorMessage {
                    feedback(error, symbol: "exclamationmark.triangle", isError: true)
                } else if let status = model.statusMessage {
                    feedback(status, symbol: "checkmark.circle", isError: false)
                }

                if model.hasNewerDictation {
                    HStack(spacing: 12) {
                        Label("A newer dictation is ready.", systemImage: "text.badge.plus")
                            .foregroundStyle(.secondary)
                        Spacer()
                        Button("Load latest") {
                            model.loadLatest()
                            copied = false
                        }
                        .disabled(model.isMutating)
                    }
                }

                if let record = model.reviewedDictation {
                    reviewEditor(record)
                    if !model.suggestions.isEmpty { suggestions }
                } else {
                    zeroState
                }
                rememberedRules
            }
            .padding(24)
        }
        .frame(minWidth: 680, idealWidth: 800, minHeight: 520, idealHeight: 700)
        .onAppear { model.openReview() }
        .onChange(of: model.draft) { copied = false }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Label("Teach Me Once", systemImage: "text.badge.checkmark")
                    .font(.title2.weight(.semibold))
                Spacer()
                if model.isLoadingLatest || model.isPreviewing || model.isMutating {
                    ProgressView().controlSize(.small)
                }
                if model.undoToken != nil {
                    Button(model.undoTitle) { model.undo() }
                        .disabled(!model.isOnline || model.isMutating)
                }
            }
            Text("Edit your last dictation, then choose the phrases JiSpr should remember.")
                .foregroundStyle(.secondary)
            Text("Remembered corrections change future dictations. Your other apps stay as you left them.")
                .font(.caption)
                .foregroundStyle(.secondary)
            if model.isVerbatim {
                Label(
                    "Verbatim mode is on. Remembered corrections apply when cleanup is enabled.",
                    systemImage: "info.circle"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            if !model.isOnline {
                Label("Start the local engine to review and remember corrections.", systemImage: "pause.circle")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var zeroState: some View {
        VStack(spacing: 10) {
            Image(systemName: "waveform.and.mic")
                .font(.system(size: 30))
                .foregroundStyle(.secondary)
            Text("Dictate something first")
                .font(.headline)
            Text("Your last dictation will be available here for a correction you choose.")
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 30)
    }

    private func reviewEditor(_ record: DictationRecord) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Your correction").font(.headline)
                    Spacer()
                    Label(record.appDisplayName, systemImage: "app")
                        .foregroundStyle(.secondary)
                }
                TextEditor(text: Binding(get: { model.draft }, set: { model.setDraft($0) }))
                    .font(.body)
                    .scrollContentBackground(.hidden)
                    .padding(8)
                    .frame(minHeight: 150, idealHeight: 180)
                    .background(.background, in: RoundedRectangle(cornerRadius: 7))
                    .overlay {
                        RoundedRectangle(cornerRadius: 7).stroke(.separator, lineWidth: 1)
                    }
                    .accessibilityLabel("Corrected dictation")
                    .disabled(model.isMutating)

                DisclosureGroup("Original dictation", isExpanded: $showsOriginal) {
                    Text(record.text)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.top, 6)
                        .foregroundStyle(.secondary)
                }
                if record.correctionsApplied > 0 {
                    Text("\(record.correctionsApplied) remembered correction(s) applied to this dictation.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                HStack {
                    Button("Review Changes") { model.reviewChanges() }
                        .buttonStyle(.borderedProminent)
                        .disabled(!model.canReviewChanges)
                    Spacer()
                    Button(copied ? "Copied" : "Copy corrected text") {
                        NSPasteboard.general.clearContents()
                        copied = NSPasteboard.general.writeObjects([model.draft as NSString])
                    }
                    .disabled(model.draft.isEmpty)
                }
            }
            .padding(8)
        }
    }

    private var suggestions: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Choose what to remember").font(.headline)
                Spacer()
                Picker("Remember in", selection: Binding(
                    get: { model.scope }, set: { model.setScope($0) }
                )) {
                    if model.reviewedDictation?.hasAppScope == true {
                        Text("This app").tag(CorrectionScope.app)
                    }
                    Text("Everywhere").tag(CorrectionScope.global)
                }
                .labelsHidden()
                .pickerStyle(.segmented)
                .frame(width: 220)
                .disabled(model.isMutating)
            }
            Text(model.scope == .app
                ? "Only future dictations in \(model.reviewedDictation?.appDisplayName ?? "this app")."
                : "Future dictations in every app.")
                .font(.caption)
                .foregroundStyle(.secondary)
            ForEach(model.suggestions) { suggestion in
                GroupBox {
                    HStack(alignment: .top, spacing: 16) {
                        phraseChange(source: suggestion.source, replacement: suggestion.replacement)
                        Spacer(minLength: 12)
                        Button("Remember") { model.remember(suggestion) }
                            .disabled(!model.isOnline || model.isMutating)
                    }
                    .padding(6)
                }
            }
        }
    }

    private var rememberedRules: some View {
        VStack(alignment: .leading, spacing: 12) {
            Divider()
            Text("Remembered corrections").font(.headline)
            if let error = model.rulesError, !error.isEmpty {
                feedback(error, symbol: "exclamationmark.triangle", isError: true)
            } else if model.rules.isEmpty {
                Text("Nothing remembered yet. Every phrase is your choice.")
                    .foregroundStyle(.secondary)
            }
            ForEach(model.rules) { rule in
                HStack(alignment: .top, spacing: 16) {
                    VStack(alignment: .leading, spacing: 5) {
                        phraseChange(source: rule.source, replacement: rule.replacement)
                        Text(friendlyScope(rule))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 12)
                    Button("Forget") { model.forget(rule) }
                        .disabled(!model.isOnline || model.isMutating)
                }
                .padding(.vertical, 5)
            }
        }
    }

    private func phraseChange(source: String, replacement: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(source).strikethrough().foregroundStyle(.secondary)
            Label(replacement, systemImage: "arrow.turn.down.right")
        }
        .textSelection(.enabled)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func friendlyScope(_ rule: CorrectionRule) -> String {
        guard let appID = rule.appID, !appID.isEmpty else { return "Everywhere" }
        for record in [model.reviewedDictation, model.latestDictation].compactMap({ $0 }) {
            if record.appID == appID, let name = record.appName, !name.isEmpty { return name }
        }
        if let appURL = NSWorkspace.shared.urlForApplication(withBundleIdentifier: appID) {
            return appURL.deletingPathExtension().lastPathComponent
        }
        return appID
    }

    private func feedback(_ message: String, symbol: String, isError: Bool) -> some View {
        Label(message, systemImage: symbol)
            .font(.callout)
            .foregroundStyle(isError ? Color.orange : Color.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
