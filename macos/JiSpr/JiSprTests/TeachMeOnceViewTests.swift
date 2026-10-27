import AppKit
import SwiftUI
import XCTest
@testable import JiSpr

final class TeachMeOnceViewTests: XCTestCase {
    @MainActor
    func testReviewAndZeroStatesRenderInLightAndDarkAppearance() throws {
        for scheme in [ColorScheme.light, .dark] {
            for hasDictation in [false, true] {
                var requestID = 0
                let model = TeachMeOnceStore { _, _ in
                    requestID += 1
                    return "preview-\(requestID)"
                }
                model.setOnline(true)
                if hasDictation {
                    model.receiveDictation(DictationRecord(
                        id: "example", text: "Please send the jisper notes to Maren.",
                        appID: "com.apple.mail", appName: "Mail", correctionsApplied: 0
                    ))
                    model.openReview()
                    model.setDraft("Please send the JiSpr notes to Maren.")
                    model.reviewChanges()
                    model.receiveReply(id: "preview-\(requestID)", result: [
                        "dictation_id": .string("example"),
                        "suggestions": .array([
                            .object(["source": .string("jisper"), "replacement": .string("JiSpr")]),
                        ]),
                    ])
                    model.updateRules([CorrectionRule(
                        id: "saved", source: "project poppy", replacement: "Project Poppy",
                        appID: nil, createdAt: "2026-10-27T12:00:00Z"
                    )])
                }
                let view = TeachMeOnceView(model: model)
                    .frame(width: 800, height: 850)
                    .environment(\.colorScheme, scheme)
                    .transaction { $0.disablesAnimations = true }
                let bitmap = try snapshot(view, scheme: scheme)
                assertVisibleContent(bitmap, scheme: scheme)
                let image = NSImage(size: bitmap.size)
                image.addRepresentation(bitmap)
                let attachment = XCTAttachment(image: image)
                attachment.name = "Teach Me Once - \(hasDictation ? "review" : "empty") - \(scheme)"
                attachment.lifetime = .keepAlways
                add(attachment)
            }
        }
    }

    /// AppKit-backed controls such as TextEditor are not captured reliably by
    /// ImageRenderer. Lay out the actual view hierarchy in a never-shown window
    /// and draw that hierarchy directly; no screen pixels are read.
    @MainActor
    private func snapshot<Content: View>(
        _ content: Content, scheme: ColorScheme
    ) throws -> NSBitmapImageRep {
        let size = NSSize(width: 800, height: 850)
        let appearance = try XCTUnwrap(NSAppearance(
            named: scheme == .dark ? .darkAqua : .aqua
        ))
        let root = content.background(Color(nsColor: .windowBackgroundColor))
        let hosting = NSHostingView(rootView: root)
        hosting.frame = NSRect(origin: .zero, size: size)
        hosting.appearance = appearance

        let window = NSWindow(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless], backing: .buffered, defer: false
        )
        window.isReleasedWhenClosed = false
        window.isExcludedFromWindowsMenu = true
        window.appearance = appearance
        window.contentView = hosting
        defer { window.close() }
        XCTAssertFalse(window.isVisible, "Snapshot windows must never be presented")

        // Let SwiftUI mount its NSScrollView/NSTextView children before asking
        // AppKit to cache them, without presenting the window.
        hosting.layoutSubtreeIfNeeded()
        RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.08))
        hosting.layoutSubtreeIfNeeded()
        hosting.displayIfNeeded()
        window.displayIfNeeded()
        let bitmap = try XCTUnwrap(hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds))
        hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
        XCTAssertFalse(window.isVisible, "Caching a view must not show its window")
        return bitmap
    }

    private func assertVisibleContent(
        _ bitmap: NSBitmapImageRep,
        scheme: ColorScheme,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertGreaterThan(bitmap.pixelsWide, 0, file: file, line: line)
        XCTAssertGreaterThan(bitmap.pixelsHigh, 0, file: file, line: line)
        guard let background = bitmap.colorAt(x: 4, y: 4)?.usingColorSpace(.deviceRGB) else {
            XCTFail("Snapshot has no readable background pixels", file: file, line: line)
            return
        }
        let backgroundLuminance = luminance(background)
        var samples = 0
        var opaqueSamples = 0
        var contrastingSamples = 0
        var colors: Set<Int> = []
        let step = max(2, min(bitmap.pixelsWide, bitmap.pixelsHigh) / 250)
        for y in stride(from: 0, to: bitmap.pixelsHigh, by: step) {
            for x in stride(from: 0, to: bitmap.pixelsWide, by: step) {
                guard let color = bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB) else {
                    continue
                }
                samples += 1
                if color.alphaComponent > 0.95 {
                    opaqueSamples += 1
                    if abs(luminance(color) - backgroundLuminance) > 0.2 {
                        contrastingSamples += 1
                    }
                }
                let red = Int(max(0, min(color.redComponent, 1)) * 31)
                let green = Int(max(0, min(color.greenComponent, 1)) * 31)
                let blue = Int(max(0, min(color.blueComponent, 1)) * 31)
                colors.insert(red * 1024 + green * 32 + blue)
            }
        }
        XCTAssertGreaterThan(samples, 1000, file: file, line: line)
        XCTAssertGreaterThan(
            Double(opaqueSamples) / Double(max(samples, 1)), 0.95,
            "A transparent capture is not a UI snapshot", file: file, line: line
        )
        XCTAssertGreaterThan(
            contrastingSamples, 150,
            "Snapshot must contain visible text and controls, not a solid background",
            file: file, line: line
        )
        XCTAssertGreaterThan(colors.count, 16, "Snapshot is blank or nearly uniform", file: file, line: line)
        if scheme == .dark {
            XCTAssertLessThan(backgroundLuminance, 0.4, "Dark appearance was not rendered", file: file, line: line)
        } else {
            XCTAssertGreaterThan(backgroundLuminance, 0.65, "Light appearance was not rendered", file: file, line: line)
        }
    }

    private func luminance(_ color: NSColor) -> CGFloat {
        0.2126 * color.redComponent + 0.7152 * color.greenComponent + 0.0722 * color.blueComponent
    }
}
