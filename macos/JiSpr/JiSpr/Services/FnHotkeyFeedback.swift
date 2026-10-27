/// The engine's successful event-tap diagnostic can race its start reply.
/// Clear Fn-specific feedback independently of the current dictation state.
struct FnHotkeyFeedback: Equatable {
    let state: DictationState
    let detail: String
    let banner: String?

    static func listenerActivated(
        state: DictationState,
        detail: String,
        banner: String?
    ) -> FnHotkeyFeedback {
        let wasPermissionWarning = state == .warning && detail == "Fn hotkey needs permission"
        let wasPermissionBanner = banner?.hasPrefix("Fn hotkey is paused.") == true
            || banner == "Permissions granted—restart the JiSpr engine to enable Fn."
        return FnHotkeyFeedback(
            state: wasPermissionWarning ? .idle : state,
            detail: wasPermissionWarning ? "Ready for your hotkey" : detail,
            banner: wasPermissionBanner ? nil : banner
        )
    }
}
