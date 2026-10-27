import AppKit
import ApplicationServices

protocol CaretBoundsProviding: Sendable {
    /// Global AX coordinates: origin at the primary display's top-left.
    func currentBounds() async -> CGRect?
}

/// Reads selection geometry only. Focused-field text and screen pixels are
/// never requested, and unavailable Accessibility permission stays silent.
actor AccessibilityCaretBoundsProvider: CaretBoundsProviding {
    private let isTrusted: @Sendable () -> Bool
    private let frontmostProcessID: @Sendable () async -> pid_t?
    private let boundsForProcess: @Sendable (pid_t) -> CGRect?

    init(
        isTrusted: @escaping @Sendable () -> Bool = { AXIsProcessTrusted() },
        frontmostProcessID: @escaping @Sendable () async -> pid_t? = {
            await MainActor.run {
                NSWorkspace.shared.frontmostApplication?.processIdentifier
            }
        },
        boundsForProcess: @escaping @Sendable (pid_t) -> CGRect? = {
            AccessibilityCaretBoundsProvider.readFocusedBounds(processID: $0)
        }
    ) {
        self.isTrusted = isTrusted
        self.frontmostProcessID = frontmostProcessID
        self.boundsForProcess = boundsForProcess
    }

    func currentBounds() async -> CGRect? {
        guard !Task.isCancelled, isTrusted(),
              let processID = await frontmostProcessID(), processID > 0,
              !Task.isCancelled,
              let bounds = boundsForProcess(processID),
              !Task.isCancelled,
              CaretPlacement.isValidCaretBounds(bounds) else { return nil }
        return bounds
    }

    nonisolated static func insertionRange(for selection: CFRange) -> CFRange? {
        guard selection.location >= 0, selection.length >= 0 else { return nil }
        let (end, overflow) = selection.location.addingReportingOverflow(selection.length)
        guard !overflow else { return nil }
        return CFRange(location: end, length: 0)
    }

    nonisolated static func supportsGeometry(role: String?, subrole: String?) -> Bool {
        guard let role,
              [kAXTextFieldRole, kAXTextAreaRole, kAXComboBoxRole]
                .contains(where: { role == ($0 as String) }) else { return false }
        let subtype = subrole?.lowercased() ?? ""
        return !subtype.contains("secure") && !subtype.contains("password")
    }

    private nonisolated static func readFocusedBounds(processID: pid_t) -> CGRect? {
        let application = AXUIElementCreateApplication(processID)
        // An app-scoped element avoids changing the process-wide AX timeout.
        // AX IPC runs on this actor's executor, never on the UI thread.
        guard AXUIElementSetMessagingTimeout(application, 0.05) == .success,
              let focusedValue = attribute(application, kAXFocusedUIElementAttribute),
              CFGetTypeID(focusedValue) == AXUIElementGetTypeID() else { return nil }
        let focused = focusedValue as! AXUIElement
        guard AXUIElementSetMessagingTimeout(focused, 0.05) == .success else { return nil }

        let role = attribute(focused, kAXRoleAttribute) as? String
        var subroleValue: CFTypeRef?
        let subroleResult = AXUIElementCopyAttributeValue(
            focused, kAXSubroleAttribute as CFString, &subroleValue
        )
        guard subroleResult == .success
                || subroleResult == .attributeUnsupported
                || subroleResult == .noValue,
              subroleResult != .success || subroleValue is String,
              supportsGeometry(role: role, subrole: subroleValue as? String),
              let rangeValue = attribute(focused, kAXSelectedTextRangeAttribute),
              CFGetTypeID(rangeValue) == AXValueGetTypeID() else { return nil }

        let selectedValue = rangeValue as! AXValue
        guard AXValueGetType(selectedValue) == .cfRange else { return nil }
        var selection = CFRange(location: 0, length: 0)
        guard AXValueGetValue(selectedValue, .cfRange, &selection),
              let insertion = insertionRange(for: selection) else { return nil }

        if let bounds = bounds(for: insertion, in: focused) { return bounds }
        // Some editors cannot answer a collapsed range. A small selection's
        // bounds still let us stay outside the edited line; large selections
        // fail the geometry guard and use the screen-bottom fallback.
        return selection.length > 0 ? bounds(for: selection, in: focused) : nil
    }

    private nonisolated static func attribute(
        _ element: AXUIElement, _ name: String
    ) -> CFTypeRef? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else {
            return nil
        }
        return value
    }

    private nonisolated static func bounds(
        for range: CFRange, in element: AXUIElement
    ) -> CGRect? {
        var range = range
        guard let parameter = AXValueCreate(.cfRange, &range) else { return nil }
        var value: CFTypeRef?
        guard AXUIElementCopyParameterizedAttributeValue(
            element, kAXBoundsForRangeParameterizedAttribute as CFString, parameter, &value
        ) == .success,
              let value, CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
        let rectValue = value as! AXValue
        guard AXValueGetType(rectValue) == .cgRect else { return nil }
        var rect = CGRect.zero
        guard AXValueGetValue(rectValue, .cgRect, &rect),
              CaretPlacement.isValidCaretBounds(rect) else { return nil }
        return rect
    }
}
