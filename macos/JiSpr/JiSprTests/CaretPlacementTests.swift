import CoreGraphics
import XCTest
@testable import JiSpr

final class CaretPlacementTests: XCTestCase {
    private let primary = CGRect(x: 0, y: 0, width: 1440, height: 900)
    private let visible = CGRect(x: 0, y: 24, width: 1440, height: 852)
    private let pill = CGSize(width: 148, height: 34)

    private func place(
        _ caret: CGRect,
        size: CGSize? = nil,
        screens: [CGRect]? = nil,
        visibleFrames: [CGRect]? = nil
    ) -> CGRect? {
        CaretPlacement.frame(
            caretBounds: caret,
            size: size ?? pill,
            screens: screens ?? [primary],
            visibleFrames: visibleFrames ?? [visible],
            primaryScreenHeight: primary.height
        )
    }

    func testZeroWidthCaretUsesItsLineAndAnEightPointGap() {
        let frame = place(CGRect(x: 200, y: 200, width: 0, height: 20))
        XCTAssertEqual(frame, CGRect(x: 200, y: 638, width: 148, height: 34))
        XCTAssertFalse(frame!.intersects(CGRect(x: 200, y: 680, width: 1, height: 20)))
    }

    func testCaretNearBottomFlipsAboveInsteadOfCoveringTheLine() {
        XCTAssertEqual(
            place(CGRect(x: 200, y: 850, width: 1, height: 20)),
            CGRect(x: 200, y: 58, width: 148, height: 34)
        )
    }

    func testRightEdgeClampsHorizontallyInsideTheVisibleFrame() {
        XCTAssertEqual(
            place(CGRect(x: 1438, y: 200, width: 1, height: 20))?.minX,
            1292
        )
    }

    func testVisibleFrameInsetsApplyOnBothHorizontalSides() {
        let inset = CGRect(x: 30, y: 24, width: 1380, height: 852)
        XCTAssertEqual(
            place(CGRect(x: 5, y: 200, width: 0, height: 20), visibleFrames: [inset])?.minX,
            30
        )
        XCTAssertEqual(
            place(CGRect(x: 1438, y: 200, width: 0, height: 20), visibleFrames: [inset])?.maxX,
            inset.maxX
        )
    }

    func testDisplayLeftOfPrimaryRetainsNegativeHorizontalOrigin() {
        let left = CGRect(x: -1920, y: 0, width: 1920, height: 1080)
        let leftVisible = CGRect(x: -1920, y: 30, width: 1920, height: 1026)
        XCTAssertEqual(
            place(
                CGRect(x: -1800, y: 100, width: 0, height: 20),
                screens: [primary, left], visibleFrames: [visible, leftVisible]
            ),
            CGRect(x: -1800, y: 738, width: 148, height: 34)
        )
    }

    func testDisplayAbovePrimaryUsesThePrimaryHeightForYConversion() {
        let above = CGRect(x: 0, y: 900, width: 1920, height: 1080)
        let aboveVisible = CGRect(x: 0, y: 900, width: 1920, height: 1056)
        XCTAssertEqual(
            place(
                CGRect(x: 300, y: -400, width: 0, height: 20),
                screens: [primary, above], visibleFrames: [visible, aboveVisible]
            ),
            CGRect(x: 300, y: 1238, width: 148, height: 34)
        )
    }

    func testDisplayBelowPrimaryRetainsNegativeAppKitVerticalOrigin() {
        let below = CGRect(x: 100, y: -1080, width: 1920, height: 1080)
        let belowVisible = CGRect(x: 100, y: -1056, width: 1920, height: 1056)
        XCTAssertEqual(
            place(
                CGRect(x: 300, y: 1200, width: 0, height: 20),
                screens: [primary, below], visibleFrames: [visible, belowVisible]
            ),
            CGRect(x: 300, y: -362, width: 148, height: 34)
        )
    }

    func testSelectionCrossingDisplaysUsesTheLargestIntersection() {
        let right = CGRect(x: 1440, y: 0, width: 1920, height: 1080)
        let rightVisible = CGRect(x: 1440, y: 24, width: 1920, height: 1032)
        XCTAssertEqual(
            place(
                CGRect(x: 1430, y: 200, width: 80, height: 20),
                screens: [primary, right], visibleFrames: [visible, rightVisible]
            )?.minX,
            1440
        )
    }

    func testOffscreenCaretAndMissingOrUnpairedDisplaysUseFallback() {
        let caret = CGRect(x: 200, y: 200, width: 1, height: 20)
        XCTAssertNil(place(CGRect(x: 10_000, y: 200, width: 1, height: 20)))
        XCTAssertNil(place(caret, screens: [], visibleFrames: []))
        XCTAssertNil(place(caret, screens: [primary], visibleFrames: []))
        XCTAssertNil(place(caret, screens: [primary, primary], visibleFrames: [visible]))
    }

    func testMalformedAndEnormousCaretBoundsUseFallback() {
        let invalid = [
            CGRect(x: CGFloat.nan, y: 200, width: 1, height: 20),
            CGRect(x: 200, y: CGFloat.infinity, width: 1, height: 20),
            CGRect(x: 200, y: 200, width: -1, height: 20),
            CGRect(x: 200, y: 200, width: 1, height: 0),
            CGRect(x: 200, y: 200, width: 1, height: -20),
            CGRect(x: 200, y: 200, width: 100_000, height: 20),
            CGRect(x: 200, y: 200, width: 1, height: 100_000),
            CGRect(x: 200, y: 200, width: 2000, height: 20),
        ]
        for caret in invalid { XCTAssertNil(place(caret), "Accepted \(caret)") }
    }

    func testInvalidPillDimensionsAndPillThatCannotFitUseFallback() {
        let caret = CGRect(x: 200, y: 200, width: 1, height: 20)
        for size in [
            CGSize(width: 0, height: 34),
            CGSize(width: 148, height: -1),
            CGSize(width: CGFloat.nan, height: 34),
            CGSize(width: 148, height: CGFloat.infinity),
            CGSize(width: 2000, height: 34),
            CGSize(width: 148, height: 1000),
        ] {
            XCTAssertNil(place(caret, size: size))
        }
    }

    func testNoSpaceAboveOrBelowDoesNotOverlayTheCaret() {
        let short = CGRect(x: 0, y: 650, width: 1440, height: 80)
        XCTAssertNil(place(
            CGRect(x: 200, y: 200, width: 1, height: 20), visibleFrames: [short]
        ))
    }

    func testMalformedDisplayGeometryAndPrimaryHeightUseFallback() {
        let caret = CGRect(x: 200, y: 200, width: 1, height: 20)
        XCTAssertNil(place(caret, screens: [CGRect(x: 0, y: 0, width: -1, height: 900)]))
        XCTAssertNil(place(caret, visibleFrames: [CGRect(x: 0, y: 0, width: 2000, height: 900)]))
        XCTAssertNil(place(caret, visibleFrames: [CGRect(x: CGFloat.nan, y: 0, width: 1440, height: 900)]))
        for height in [CGFloat.nan, .infinity, 0, -1] {
            XCTAssertNil(CaretPlacement.frame(
                caretBounds: caret, size: pill, screens: [primary],
                visibleFrames: [visible], primaryScreenHeight: height
            ))
        }
    }
}
