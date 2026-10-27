import SwiftUI
import XCTest
@testable import JiSpr

final class LivingCaretViewTests: XCTestCase {
    @MainActor
    func testLivingCaretStatesRenderInLightAndDarkAppearance() throws {
        for scheme in [ColorScheme.light, .dark] {
            let gallery = VStack(alignment: .leading, spacing: 20) {
                Text("JiSpr · Living Caret")
                    .font(.title3.weight(.semibold))
                ForEach(["compact", "expanded"], id: \.self) { style in
                    HStack(spacing: 24) {
                        ForEach([DictationState.recording, .processing, .inserted], id: \.self) { state in
                            VStack(alignment: .leading, spacing: 12) {
                                Text(state.title).font(.caption).foregroundStyle(.secondary)
                                HStack(spacing: 1) {
                                    Text("Your words go here")
                                    Rectangle().fill(.tint).frame(width: 1, height: 18)
                                }
                                FloatingPillView(configuration: FloatingPillConfiguration(
                                    enabled: true,
                                    style: style,
                                    state: state,
                                    detail: "",
                                    audioLevel: 0.65,
                                    hotkey: "fn",
                                    position: "caret"
                                ))
                            }
                            .frame(width: 170, alignment: .leading)
                        }
                    }
                }
            }
            .padding(28)
            .background(.background)
            .environment(\.colorScheme, scheme)
            .transaction { $0.disablesAnimations = true }
            let renderer = ImageRenderer(content: gallery)
            renderer.scale = 2
            let image = try XCTUnwrap(renderer.nsImage)
            let attachment = XCTAttachment(image: image)
            attachment.name = "Living Caret - \(scheme)"
            attachment.lifetime = .keepAlways
            add(attachment)
        }
    }
}
