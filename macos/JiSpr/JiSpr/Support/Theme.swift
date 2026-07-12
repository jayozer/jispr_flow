import AppKit
import SwiftUI

private extension NSColor {
    convenience init(hex: UInt32) {
        self.init(
            calibratedRed: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1
        )
    }

    static func adaptive(light: UInt32, dark: UInt32) -> NSColor {
        NSColor(name: nil) { appearance in
            let match = appearance.bestMatch(from: [.aqua, .darkAqua])
            return NSColor(hex: match == .darkAqua ? dark : light)
        }
    }
}
enum JiSprTheme {
    static let canvas = Color(nsColor: .adaptive(light: 0xF4EBDD, dark: 0x24241F))
    static let surface = Color(nsColor: .adaptive(light: 0xFFF9F0, dark: 0x302F29))
    static let sage = Color(nsColor: .adaptive(light: 0x8FA98B, dark: 0xA8C2A0))
    static let orange = Color(nsColor: .adaptive(light: 0xD98552, dark: 0xE8A06C))
    static let ink = Color(nsColor: .adaptive(light: 0x2F332D, dark: 0xF4EFE5))
    static let border = Color(nsColor: .adaptive(light: 0xDED2C1, dark: 0x46443C))
}

struct CardModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(18)
            .background(JiSprTheme.surface, in: RoundedRectangle(cornerRadius: 14))
            .overlay {
                RoundedRectangle(cornerRadius: 14)
                    .stroke(JiSprTheme.border.opacity(0.75), lineWidth: 1)
            }
            .shadow(color: .black.opacity(0.05), radius: 8, y: 3)
    }
}

extension View {
    func jisprCard() -> some View { modifier(CardModifier()) }
}
