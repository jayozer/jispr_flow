import SwiftUI

struct SetupView: View {
    let model: SetupStore

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if let error = model.errorMessage {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .font(.callout)
                            .foregroundStyle(.orange)
                    }
                    if !model.isOnline {
                        Label("Waiting for the local engine. You can return to setup anytime.", systemImage: "pause.circle")
                            .foregroundStyle(.secondary)
                    }
                    switch model.phase {
                    case .welcome: welcome
                    case .models: SetupModelsView(model: model)
                    case .permissions: permissions
                    case .practice: SetupPracticeView(model: model)
                    case .complete: complete
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(28)
            }
            Divider()
            footer
        }
        .frame(minWidth: 720, idealWidth: 820, minHeight: 610, idealHeight: 760)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 14) {
                Image(systemName: "waveform")
                    .font(.system(size: 24, weight: .medium))
                    .foregroundStyle(Color.accentColor)
                    .frame(width: 48, height: 48)
                    .background(Color.accentColor.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))
                VStack(alignment: .leading, spacing: 4) {
                    Text("Make JiSpr yours").font(.title2.weight(.semibold))
                    Text("A few simple steps to your first private dictation.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if model.isLoadingCatalog || model.isApplying {
                    ProgressView().controlSize(.small)
                }
            }
            HStack(spacing: 12) {
                ForEach(SetupPhase.allCases.filter { $0 != .complete }) { phase in
                    HStack(spacing: 6) {
                        Image(systemName: model.phase.rawValue > phase.rawValue
                            ? "checkmark.circle.fill" : "\(phase.rawValue + 1).circle\(model.phase == phase ? ".fill" : "")")
                        Text(phase.title).font(.caption.weight(model.phase == phase ? .semibold : .regular))
                    }
                    .foregroundStyle(model.phase == phase ? Color.primary : Color.secondary)
                    if phase != .practice { Spacer(minLength: 4) }
                }
            }
            .accessibilityElement(children: .combine)
            if let progress = model.download, progress.state.isActive {
                HStack(spacing: 12) {
                    if let fraction = progress.fractionCompleted {
                        ProgressView(value: fraction).frame(width: 160)
                    } else {
                        ProgressView().controlSize(.small)
                    }
                    Text(progress.state == .downloading ? progress.byteLabel : progress.state.title)
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Cancel") { model.cancelActiveDownload() }
                        .disabled(progress.state == .cancelling)
                }
            }
        }
        .padding(24)
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 22) {
            Text("Your voice. Your Mac.").font(.largeTitle.weight(.semibold))
            Text("Speak naturally, and JiSpr puts your words where you're writing. Speech recognition runs on this Mac, even when you're offline after setup.")
                .font(.title3)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 16) {
                welcomeRow("Choose a local speech model", detail: "Download once, then keep your audio on your Mac.", symbol: "internaldrive")
                welcomeRow("Check the three permissions", detail: "You decide when JiSpr may listen and insert text.", symbol: "hand.raised")
                welcomeRow("Try one real dictation", detail: "Finish after your words appear in the practice field.", symbol: "text.cursor")
            }
            .padding(18)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 14))
            HStack {
                Text("What language do you use?").font(.headline)
                Spacer()
                languagePicker
                    .frame(width: 260)
            }
            Text("Your current settings stay as they are until you choose Apply.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func welcomeRow(_ title: String, detail: String, symbol: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol).frame(width: 22).foregroundStyle(Color.accentColor)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.headline)
                Text(detail).foregroundStyle(.secondary)
            }
        }
    }

    private var languagePicker: some View {
        Picker("Language", selection: Binding(get: { model.language }, set: { model.setLanguage($0) })) {
            ForEach(model.availableLanguages, id: \.self) { code in
                Text(SetupStore.languageTitle(code)).tag(code)
            }
        }
        .labelsHidden()
        .disabled(model.isApplying)
    }

    private var permissions: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Give JiSpr a little room to work").font(.title2.weight(.semibold))
            Text("macOS asks for these permissions separately. Allow each one, then return here.")
                .foregroundStyle(.secondary)
            ForEach(SetupPermission.allCases) { permission in
                GroupBox {
                    HStack(spacing: 14) {
                        Image(systemName: model.isGranted(permission) ? "checkmark.circle.fill" : "circle")
                            .font(.title2)
                            .foregroundStyle(model.isGranted(permission) ? Color.green : Color.secondary)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(permission.title).font(.headline)
                            Text(permission.detail).foregroundStyle(.secondary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 6) {
                            if model.isGranted(permission) {
                                Text("Granted").foregroundStyle(.secondary)
                            } else {
                                Button("Request Access") { model.requestPermission(permission) }
                            }
                            Button("Open Settings") { model.openPermissionSettings(permission) }
                                .font(.caption)
                        }
                    }
                    .padding(10)
                }
            }
            Label("Your voice and dictation are processed locally.", systemImage: "lock")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var complete: some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.circle.fill").font(.system(size: 48)).foregroundStyle(.green)
            Text("You're ready to flow").font(.largeTitle.weight(.semibold))
            Text("Your model is ready and your practice words arrived. JiSpr is waiting in the menu bar.")
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }

    private var footer: some View {
        HStack(spacing: 12) {
            Button("Later") { model.later() }.disabled(model.isApplying)
            Spacer()
            if model.phase != .welcome, model.phase != .complete {
                Button("Back") { model.back() }.disabled(model.isApplying)
            }
            switch model.phase {
            case .welcome:
                Button("Choose a model") { model.continueFromWelcome() }
                    .disabled(!model.isOnline || model.catalog == nil)
                    .buttonStyle(.borderedProminent)
            case .models:
                if model.hasActiveDownload {
                    Button(model.download?.state == .cancelling ? "Cancelling…" : "Cancel download") {
                        model.cancelActiveDownload()
                    }
                    .disabled(model.download?.state == .cancelling)
                } else if let selected = model.selectedModel, !selected.isReady {
                    Button(downloadActionTitle(selected)) {
                        model.downloadSelectedModel()
                    }
                    .disabled(!model.canDownloadSelection)
                    .buttonStyle(.borderedProminent)
                } else {
                    Button(model.isApplying ? "Applying…" : "Apply and continue") { model.applySelection() }
                        .disabled(!model.canApplySelection)
                        .buttonStyle(.borderedProminent)
                }
            case .permissions:
                Button("Continue") { model.continueToPractice() }
                    .disabled(!model.isOnline || !model.hasAppliedSelection || !model.allPermissionsGranted)
                    .buttonStyle(.borderedProminent)
            case .practice:
                Button("Finish setup") { model.finish() }
                    .disabled(!model.canFinish)
                    .buttonStyle(.borderedProminent)
            case .complete:
                Button("Done") { model.later() }.buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
    }

    private func downloadActionTitle(_ selected: SetupSpeechModel) -> String {
        if selected.installed { return "Verify model" }
        if model.download?.modelID == selected.id,
           model.download?.state == .error || model.download?.state == .cancelled {
            return "Retry download"
        }
        return "Download model"
    }
}
