import AppKit
import SwiftUI
import XCTest
@testable import JiSpr

final class SetupViewTests: XCTestCase {
    @MainActor
    func testGuidedSetupScreensRenderRealContentInLightAndDarkAppearance() throws {
        for scheme in [ColorScheme.light, .dark] {
            for screen in ["welcome", "models", "download", "permissions", "practice"] {
                let commands = SetupTestCommands()
                let model = SetupStore { commands.send($0, payload: $1) }
                model.beginSession()
                model.receiveReply(id: commands.lastID("setup_catalog"), result: SetupTestFixtures.catalog(
                    installed: screen != "download", verified: screen != "download"
                ))
                if screen != "welcome" { model.openModels() }
                if screen == "download" {
                    model.downloadSelectedModel()
                    let progress = SetupTestFixtures.progress()
                    model.receiveReply(id: commands.lastID("model_download"), result: [
                        "download": SetupTestFixtures.progressJSON(progress),
                    ])
                }
                if screen == "permissions" || screen == "practice" {
                    model.applySelection()
                    model.receiveReply(id: commands.lastID("setup_apply"), result: ["applied": .bool(true)])
                    model.updatePermissions(accessibility: true, inputMonitoring: true, microphone: screen == "practice")
                }
                if screen == "practice" {
                    model.continueToPractice()
                    model.startPractice()
                    model.updateEngine(state: .recording, detail: "Listening", audioLevel: 0.65)
                    model.setPracticeText("JiSpr keeps my words on this Mac.")
                    model.receivePracticeDictation(id: "example", text: "JiSpr keeps my words on this Mac.")
                    model.updateEngine(state: .inserted, detail: "Text inserted", audioLevel: 0)
                }
                let content = SetupView(model: model)
                    .frame(width: 820, height: 850)
                    .environment(\.colorScheme, scheme)
                    .transaction { $0.disablesAnimations = true }
                let bitmap = try snapshot(content, scheme: scheme)
                assertVisibleContent(bitmap, scheme: scheme)
                let image = NSImage(size: bitmap.size)
                image.addRepresentation(bitmap)
                let attachment = XCTAttachment(image: image)
                attachment.name = "Guided Setup - \(screen) - \(scheme)"
                attachment.lifetime = .keepAlways
                add(attachment)
            }
        }
    }

    @MainActor
    private func snapshot<Content: View>(
        _ content: Content, scheme: ColorScheme
    ) throws -> NSBitmapImageRep {
        let size = NSSize(width: 820, height: 850)
        let appearance = try XCTUnwrap(NSAppearance(named: scheme == .dark ? .darkAqua : .aqua))
        let hosting = NSHostingView(rootView: content.background(Color(nsColor: .windowBackgroundColor)))
        hosting.frame = NSRect(origin: .zero, size: size)
        hosting.appearance = appearance
        let window = NSWindow(contentRect: hosting.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.isExcludedFromWindowsMenu = true
        window.appearance = appearance
        window.contentView = hosting
        defer { window.close() }
        XCTAssertFalse(window.isVisible)
        hosting.layoutSubtreeIfNeeded()
        RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.08))
        hosting.layoutSubtreeIfNeeded()
        hosting.displayIfNeeded()
        window.displayIfNeeded()
        let bitmap = try XCTUnwrap(hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds))
        hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
        XCTAssertFalse(window.isVisible)
        return bitmap
    }

    private func assertVisibleContent(
        _ bitmap: NSBitmapImageRep, scheme: ColorScheme,
        file: StaticString = #filePath, line: UInt = #line
    ) {
        let background = bitmap.colorAt(x: 4, y: 4)?.usingColorSpace(.deviceRGB)
        guard let background else { XCTFail("Missing snapshot pixels", file: file, line: line); return }
        let base = luminance(background)
        var samples = 0
        var opaque = 0
        var contrasting = 0
        var colors: Set<Int> = []
        let step = max(2, min(bitmap.pixelsWide, bitmap.pixelsHigh) / 250)
        for y in stride(from: 0, to: bitmap.pixelsHigh, by: step) {
            for x in stride(from: 0, to: bitmap.pixelsWide, by: step) {
                guard let color = bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB) else { continue }
                samples += 1
                if color.alphaComponent > 0.95 {
                    opaque += 1
                    if abs(luminance(color) - base) > 0.2 { contrasting += 1 }
                }
                let r = Int(max(0, min(color.redComponent, 1)) * 31)
                let g = Int(max(0, min(color.greenComponent, 1)) * 31)
                let b = Int(max(0, min(color.blueComponent, 1)) * 31)
                colors.insert(r * 1024 + g * 32 + b)
            }
        }
        XCTAssertGreaterThan(samples, 1000, file: file, line: line)
        XCTAssertGreaterThan(Double(opaque) / Double(max(samples, 1)), 0.95, "Transparent capture", file: file, line: line)
        XCTAssertGreaterThan(contrasting, 150, "No visible text or controls", file: file, line: line)
        XCTAssertGreaterThan(colors.count, 16, "Blank or uniform capture", file: file, line: line)
        if scheme == .dark {
            XCTAssertLessThan(base, 0.4, file: file, line: line)
        } else {
            XCTAssertGreaterThan(base, 0.65, file: file, line: line)
        }
    }

    private func luminance(_ color: NSColor) -> CGFloat {
        0.2126 * color.redComponent + 0.7152 * color.greenComponent + 0.0722 * color.blueComponent
    }
}
