import SwiftUI

struct SetupPracticeView: View {
    let model: SetupStore
    @FocusState private var fieldFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Let your words arrive").font(.title2.weight(.semibold))
            Text(model.captureMode == "hands-free"
                ? "Click Start practice, keep the cursor in the field, then speak and pause."
                : "Click Start practice, hold \(model.hotkeyTitle), speak, and release the key.")
                .foregroundStyle(.secondary)
            Text("Try: “JiSpr keeps my words on this Mac.”")
                .font(.headline)
            HStack {
                Button(model.practiceStartPending ? "Starting…" : model.practiceStarted ? "Try again" : "Start practice") {
                    fieldFocused = true
                    model.startPractice()
                }
                .buttonStyle(.borderedProminent)
                .disabled(!model.isOnline || !model.allPermissionsGranted || model.engineState.isBusy || model.practiceStartPending)
                Spacer()
                Label(practiceStatus,
                      systemImage: model.engineState.isBusy ? "waveform" : "mic")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            TextEditor(text: Binding(get: { model.practiceText }, set: { model.setPracticeText($0) }))
                .font(.system(size: 18))
                .scrollContentBackground(.hidden)
                .padding(12)
                .frame(minHeight: 170, idealHeight: 210)
                .background(.background, in: RoundedRectangle(cornerRadius: 12))
                .overlay {
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(fieldFocused ? Color.accentColor : Color.primary.opacity(0.15), lineWidth: fieldFocused ? 2 : 1)
                }
                .overlay(alignment: .topLeading) {
                    if model.practiceText.isEmpty {
                        Text("Your dictated words will appear here…")
                            .font(.system(size: 18))
                            .foregroundStyle(.tertiary)
                            .padding(17)
                            .allowsHitTesting(false)
                    }
                }
                .focused($fieldFocused)
                .accessibilityLabel("Practice dictation field")
            HStack(spacing: 12) {
                Image(systemName: "mic.fill").foregroundStyle(.secondary)
                ProgressView(value: model.audioLevel)
                    .progressViewStyle(.linear)
                    .accessibilityLabel("Microphone input level")
                Text("Microphone level").font(.caption).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 9) {
                evidence("Recording detected", complete: model.practiceSawRecording)
                evidence("Dictated words arrived in this field", complete: model.canFinish)
            }
            if model.engineState == .error || model.engineState == .warning {
                Label(model.engineDetail, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
            }
            Text("Finish becomes available after a real dictation arrives here. Choose Later whenever you need to pause.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .onChange(of: model.practiceStarted) {
            if model.practiceStarted { fieldFocused = true }
        }
    }

    private func evidence(_ title: String, complete: Bool) -> some View {
        Label(title, systemImage: complete ? "checkmark.circle.fill" : "circle")
            .font(.callout)
            .foregroundStyle(complete ? Color.green : Color.secondary)
    }

    private var practiceStatus: String {
        if model.practiceStartPending || model.engineState == .offline {
            return model.engineDetail.isEmpty ? "Preparing speech recognition…" : model.engineDetail
        }
        if model.engineState.isBusy || model.engineState == .error || model.engineState == .warning {
            return model.engineState.title
        }
        return model.practiceStarted ? "Ready for your voice" : "Start when you're ready"
    }
}
