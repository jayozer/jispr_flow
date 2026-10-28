import SwiftUI

struct SetupModelsView: View {
    let model: SetupStore

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Find your local voice").font(.title2.weight(.semibold))
                    Text("Choose a model, prepare it once, then apply when you're ready.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button { model.refreshCatalog() } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .disabled(!model.isOnline || model.isLoadingCatalog)
            }
            if let notice = model.catalog?.configurationNotice, !notice.isEmpty {
                Label(notice, systemImage: "info.circle")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            ForEach(model.catalog?.models ?? []) { speechModel in
                modelCard(speechModel)
            }
            if let selected = model.selectedModel {
                HStack {
                    Text("Language").font(.headline)
                    Spacer()
                    Picker("Language", selection: Binding(
                        get: { model.language }, set: { model.setLanguage($0) }
                    )) {
                        ForEach(model.availableLanguages, id: \.self) { code in
                            Text(SetupStore.languageTitle(code)).tag(code)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 260)
                    .disabled(model.isApplying)
                }
                if let notice = model.languageNotice {
                    Text(notice).font(.caption).foregroundStyle(.secondary)
                }
                if !selected.isReady {
                    Text(selected.installed
                        ? "Choose Verify model to check the files already on this Mac."
                        : "Choose Download model to prepare it. An internet connection is needed.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if let progress = model.download { downloadStatus(progress) }
            if model.isStartingDownload, model.download == nil {
                HStack(spacing: 10) {
                    ProgressView().controlSize(.small)
                    Text("Preparing your model…").foregroundStyle(.secondary)
                    Spacer()
                    Button("Cancel") { model.cancelActiveDownload() }
                }
            }
            polishChoice
            Text("Downloads do not change your active model. Apply saves the choices above.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func modelCard(_ speechModel: SetupSpeechModel) -> some View {
        let selected = model.selectedModelID == speechModel.id
        return Button { model.selectModel(speechModel.id) } label: {
            HStack(alignment: .top, spacing: 13) {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                    .font(.title3)
                    .foregroundStyle(selected ? Color.accentColor : Color.secondary)
                    .padding(.top, 2)
                VStack(alignment: .leading, spacing: 7) {
                    HStack {
                        Text(speechModel.name).font(.headline)
                        Spacer()
                        Text(status(speechModel))
                            .font(.caption.weight(.medium))
                            .foregroundStyle(speechModel.isReady ? Color.green : Color.secondary)
                    }
                    Text(speechModel.description).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 12) {
                        Label(speechModel.sizeHint, systemImage: "internaldrive")
                        Text(speechModel.languages == ["en"] ? "English only" : "Multilingual")
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    if let reason = speechModel.unavailableReason, !speechModel.available {
                        Text(reason).font(.caption).foregroundStyle(.orange)
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(selected ? Color.accentColor.opacity(0.07) : Color.primary.opacity(0.025),
                        in: RoundedRectangle(cornerRadius: 12))
            .overlay {
                RoundedRectangle(cornerRadius: 12)
                    .stroke(selected ? Color.accentColor : Color.primary.opacity(0.12), lineWidth: selected ? 1.5 : 1)
            }
            .contentShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
        .disabled(!speechModel.available || model.isApplying)
        .accessibilityLabel("\(speechModel.name), \(status(speechModel))")
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func downloadStatus(_ progress: DownloadProgress) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(progress.state.title).font(.headline)
                        Text(model.catalog?.models.first(where: { $0.id == progress.modelID })?.name ?? "Speech model")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if progress.state.isActive {
                        Button(progress.state == .cancelling ? "Cancelling…" : "Cancel") {
                            model.cancelActiveDownload()
                        }
                        .disabled(progress.state == .cancelling)
                    } else if progress.state == .completed {
                        Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                    }
                }
                if progress.state.isActive {
                    if let fraction = progress.fractionCompleted {
                        ProgressView(value: fraction)
                    } else {
                        ProgressView().progressViewStyle(.linear)
                    }
                }
                if progress.state == .downloading || progress.downloadedBytes > 0 {
                    Text(progress.byteLabel).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
                if let name = progress.fileName, !name.isEmpty, progress.state.isActive {
                    Text(name).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                }
                if let error = progress.error, !error.isEmpty {
                    Text(error).font(.callout).foregroundStyle(.orange)
                }
                if progress.state == .cancelled {
                    Text("You can retry when you're ready.").font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(8)
        }
    }

    private var polishChoice: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                Text("Writing cleanup").font(.headline)
                Picker("Writing cleanup", selection: Binding(
                    get: { model.polishBackend }, set: { model.setPolishBackend($0) }
                )) {
                    Text("Local rules — ready without another app").tag("rules")
                    Text("LM Studio — use my local writing model").tag("lmstudio")
                }
                .labelsHidden()
                .disabled(model.isApplying)
                Text(model.polishBackend == "rules"
                    ? "A simple local starting point. You can add AI writing polish later."
                    : "Keep LM Studio running with a loaded model and its local server enabled.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func status(_ value: SetupSpeechModel) -> String {
        if !value.available { return "Unavailable on this Mac" }
        if value.isReady { return "Ready offline" }
        return value.installed ? "Needs verification" : "Not downloaded"
    }

}
