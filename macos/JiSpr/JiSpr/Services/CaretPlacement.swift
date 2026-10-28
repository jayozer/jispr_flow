import CoreGraphics

/// Converts AX's top-left desktop coordinates into AppKit's bottom-left space.
/// No screen or window APIs are used here, so layout can be checked headlessly.
enum CaretPlacement {
    static func frame(
        caretBounds: CGRect,
        size: CGSize,
        screens: [CGRect],
        visibleFrames: [CGRect],
        primaryScreenHeight: CGFloat
    ) -> CGRect? {
        guard isValidCaretBounds(caretBounds),
              size.width.isFinite, size.height.isFinite,
              size.width > 0, size.height > 0,
              primaryScreenHeight.isFinite, primaryScreenHeight > 0,
              primaryScreenHeight <= 32_768,
              !screens.isEmpty, screens.count == visibleFrames.count else {
            return nil
        }

        let caret = CGRect(
            x: caretBounds.minX,
            y: primaryScreenHeight - caretBounds.maxY,
            width: caretBounds.width,
            height: caretBounds.height
        )
        // AX often reports an insertion point with zero width. Give only the
        // screen-intersection probe width; retain the exact line for placement.
        let probe = CGRect(
            x: caret.minX, y: caret.minY,
            width: max(caret.width, 1), height: caret.height
        )

        var destination: (screen: CGRect, visible: CGRect, overlap: CGFloat)?
        for (screen, visible) in zip(screens, visibleFrames) {
            guard isValidDisplayFrame(screen), isValidDisplayFrame(visible),
                  screen.contains(visible) else { return nil }
            let overlap = screen.intersection(probe)
            guard !overlap.isNull, !overlap.isEmpty else { continue }
            let area = overlap.width * overlap.height
            if destination == nil || area > destination!.overlap {
                destination = (screen, visible, area)
            }
        }

        guard let destination,
              caret.width <= destination.screen.width,
              caret.height <= destination.screen.height,
              size.width <= destination.visible.width,
              size.height <= destination.visible.height else { return nil }

        let visible = destination.visible
        let x = min(max(caret.minX, visible.minX), visible.maxX - size.width)
        let gap: CGFloat = 8
        let belowY = caret.minY - gap - size.height
        let aboveY = caret.maxY + gap
        let y: CGFloat
        if belowY >= visible.minY, belowY + size.height <= visible.maxY {
            y = belowY
        } else if aboveY >= visible.minY, aboveY + size.height <= visible.maxY {
            y = aboveY
        } else {
            // Do not clamp vertically across the line being edited. The caller
            // can use its ordinary screen-bottom placement instead.
            return nil
        }
        return CGRect(x: x, y: y, width: size.width, height: size.height)
    }

    static func isValidCaretBounds(_ bounds: CGRect) -> Bool {
        // CGRect.width/height standardize negative sizes. Validate the raw AX
        // dimensions before using any standardized geometry properties.
        bounds.origin.x.isFinite && bounds.origin.y.isFinite
            && bounds.size.width.isFinite && bounds.size.height.isFinite
            && abs(bounds.origin.x) <= 1_000_000
            && abs(bounds.origin.y) <= 1_000_000
            && bounds.size.width >= 0 && bounds.size.width <= 16_384
            && bounds.size.height > 0 && bounds.size.height <= 256
    }

    private static func isValidDisplayFrame(_ frame: CGRect) -> Bool {
        frame.origin.x.isFinite && frame.origin.y.isFinite
            && frame.size.width.isFinite && frame.size.height.isFinite
            && abs(frame.origin.x) <= 1_000_000
            && abs(frame.origin.y) <= 1_000_000
            && frame.size.width > 0 && frame.size.width <= 32_768
            && frame.size.height > 0 && frame.size.height <= 32_768
    }
}
