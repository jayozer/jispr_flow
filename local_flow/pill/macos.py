"""Native macOS floating recording pill.

AppKit is imported only when the pill is constructed. The dictation loop
runs on a daemon worker while AppKit owns the main thread; status and meter
updates marshal back through ``PyObjCTools.AppHelper.callAfter``.
"""

from __future__ import annotations

import signal
import sys
import threading
from collections.abc import Callable

from local_flow.errors import LocalFlowError
from local_flow.pill.reporter import PillReporter
from local_flow.pill.state import PillStateMachine, PillView
from local_flow.status import CompositeReporter, StatusReporter

_EXPANDED_WIDTH = 280.0
_EXPANDED_HEIGHT = 56.0
_COMPACT_IDLE_WIDTH = 72.0
_COMPACT_IDLE_HEIGHT = 8.0
_COMPACT_ACTIVE_WIDTH = 104.0
_COMPACT_ACTIVE_HEIGHT = 20.0
_COMPACT_ERROR_WIDTH = 240.0
_COMPACT_ERROR_HEIGHT = 36.0
_BOTTOM_MARGIN = 18.0

_PILL_VIEW_CLASS = None
_PULSE_CLASS = None


def _pill_layout(style: str, view: PillView) -> tuple[float, float]:
    """Return the native window size for one visual state.

    Kept pure so compact/expanded contracts stay headlessly testable without
    importing AppKit.
    """
    if style == "expanded":
        return _EXPANDED_WIDTH, _EXPANDED_HEIGHT
    if view.kind == "idle":
        return _COMPACT_IDLE_WIDTH, _COMPACT_IDLE_HEIGHT
    if view.kind == "error":
        return _COMPACT_ERROR_WIDTH, _COMPACT_ERROR_HEIGHT
    return _COMPACT_ACTIVE_WIDTH, _COMPACT_ACTIVE_HEIGHT


def _draw_compact(AppKit, bounds, state: PillView) -> None:
    """Draw the minimal Apple/Wispr-inspired surface."""
    accents = {
        "idle": (0.58, 0.61, 0.68),
        "recording": (1.0, 0.32, 0.38),
        "processing": (0.62, 0.42, 1.0),
        "inserted": (0.24, 0.82, 0.52),
        "error": (1.0, 0.36, 0.28),
    }
    red, green, blue = accents[state.kind]
    accent = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
        red, green, blue, 1.0
    )
    muted = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
        red, green, blue, 0.34
    )
    background = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
        0.055, 0.06, 0.075, 0.90
    )
    outline = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
        1.0, 1.0, 1.0, 0.12
    )
    radius = bounds.size.height / 2.0
    shell = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        bounds, radius, radius
    )
    background.setFill()
    shell.fill()
    outline.setStroke()
    shell.setLineWidth_(0.75)
    shell.stroke()

    center_y = bounds.size.height / 2.0
    if state.show_meter:
        pattern = (0.28, 0.46, 0.68, 0.88, 1.0, 0.84, 0.64, 0.92, 0.66, 0.44, 0.26)
        bar_width = 3.0
        gap = 3.0
        total_width = len(pattern) * bar_width + (len(pattern) - 1) * gap
        base_x = (bounds.size.width - total_width) / 2.0
        energy = 0.22 + state.level * 0.78
        maximum = bounds.size.height - 4.0
        for index, weight in enumerate(pattern):
            height = max(2.0, maximum * weight * energy)
            rect = AppKit.NSMakeRect(
                base_x + index * (bar_width + gap),
                center_y - height / 2.0,
                bar_width,
                height,
            )
            bar = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                rect, 1.5, 1.5
            )
            accent.setFill()
            bar.fill()
        return

    if state.kind == "processing":
        muted.setStroke()
        track = AppKit.NSBezierPath.bezierPath()
        track.moveToPoint_((18.0, center_y))
        track.lineToPoint_((bounds.size.width - 18.0, center_y))
        track.setLineWidth_(2.0)
        track.setLineCapStyle_(AppKit.NSLineCapStyleRound)
        track.stroke()
        for index, alpha in enumerate((0.38, 0.68, 1.0)):
            dot_color = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                red, green, blue, alpha
            )
            dot_color.setFill()
            x = bounds.size.width / 2.0 - 10.0 + index * 10.0
            AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                AppKit.NSMakeRect(x - 2.0, center_y - 2.0, 4.0, 4.0)
            ).fill()
        return

    if state.kind == "error":
        accent.setFill()
        AppKit.NSBezierPath.bezierPathWithOvalInRect_(
            AppKit.NSMakeRect(13.0, center_y - 5.0, 10.0, 10.0)
        ).fill()
        font = AppKit.NSFont.systemFontOfSize_weight_(
            12.0, AppKit.NSFontWeightMedium
        )
        attributes = {
            AppKit.NSFontAttributeName: font,
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
        }
        AppKit.NSString.stringWithString_(state.label).drawInRect_withAttributes_(
            AppKit.NSMakeRect(32.0, center_y - 8.0, bounds.size.width - 44.0, 17.0),
            attributes,
        )
        return

    line = AppKit.NSBezierPath.bezierPath()
    inset = 16.0 if state.kind == "inserted" else 18.0
    line.moveToPoint_((inset, center_y))
    line.lineToPoint_((bounds.size.width - inset, center_y))
    line.setLineWidth_(3.0 if state.kind == "inserted" else 2.0)
    line.setLineCapStyle_(AppKit.NSLineCapStyleRound)
    (accent if state.kind == "inserted" else muted).setStroke()
    line.stroke()

    if state.kind == "idle":
        accent.setFill()
        AppKit.NSBezierPath.bezierPathWithOvalInRect_(
            AppKit.NSMakeRect(bounds.size.width / 2.0 - 1.5, center_y - 1.5, 3.0, 3.0)
        ).fill()


def _make_pill_view_class(AppKit, objc):
    global _PILL_VIEW_CLASS
    if _PILL_VIEW_CLASS is not None:
        return _PILL_VIEW_CLASS

    class JisprRecordingPillView(AppKit.NSView):
        def initWithFrame_(self, frame):
            self = objc.super(JisprRecordingPillView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.pill_view = PillView("idle", "Ready")
            self.pill_style = "compact"
            return self

        def isOpaque(self):
            return False

        def drawRect_(self, _dirty_rect):
            bounds = self.bounds()
            state = self.pill_view

            if self.pill_style == "compact":
                _draw_compact(AppKit, bounds, state)
                return

            background = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                0.075, 0.082, 0.102, 0.96
            )
            outline = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                1.0, 1.0, 1.0, 0.10
            )
            shell = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bounds, 20.0, 20.0
            )
            background.setFill()
            shell.fill()
            outline.setStroke()
            shell.setLineWidth_(1.0)
            shell.stroke()

            accents = {
                "idle": (0.48, 0.52, 0.62),
                "recording": (1.0, 0.32, 0.38),
                "processing": (0.62, 0.42, 1.0),
                "inserted": (0.24, 0.82, 0.52),
                "error": (1.0, 0.36, 0.28),
            }
            red, green, blue = accents[state.kind]
            accent = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                red, green, blue, 1.0
            )
            muted = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                red, green, blue, 0.28
            )

            label_x = 50.0
            if state.show_meter:
                pattern = (0.34, 0.58, 0.88, 1.0, 0.78, 0.52, 0.30)
                bar_width = 4.0
                gap = 4.0
                base_x = 18.0
                center_y = bounds.size.height / 2.0
                energy = 0.18 + state.level * 0.82
                for index, weight in enumerate(pattern):
                    height = 5.0 + 31.0 * weight * energy
                    rect = AppKit.NSMakeRect(
                        base_x + index * (bar_width + gap),
                        center_y - height / 2.0,
                        bar_width,
                        height,
                    )
                    bar = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                        rect, 2.0, 2.0
                    )
                    accent.setFill()
                    bar.fill()
                label_x = 86.0
            elif state.kind == "processing":
                for index, alpha in enumerate((0.35, 0.65, 1.0)):
                    dot_color = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                        red, green, blue, alpha
                    )
                    dot_color.setFill()
                    dot = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                        AppKit.NSMakeRect(18.0 + index * 9.0, 24.0, 6.0, 6.0)
                    )
                    dot.fill()
            elif state.kind == "inserted":
                check = AppKit.NSBezierPath.bezierPath()
                check.moveToPoint_((18.0, 28.0))
                check.lineToPoint_((24.0, 22.0))
                check.lineToPoint_((35.0, 34.0))
                accent.setStroke()
                check.setLineWidth_(3.0)
                check.setLineCapStyle_(AppKit.NSLineCapStyleRound)
                check.stroke()
            elif state.kind == "error":
                accent.setFill()
                circle = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                    AppKit.NSMakeRect(17.0, 19.0, 18.0, 18.0)
                )
                circle.fill()
                mark = AppKit.NSBezierPath.bezierPath()
                mark.moveToPoint_((26.0, 31.5))
                mark.lineToPoint_((26.0, 25.5))
                AppKit.NSColor.whiteColor().setStroke()
                mark.setLineWidth_(2.0)
                mark.setLineCapStyle_(AppKit.NSLineCapStyleRound)
                mark.stroke()
                AppKit.NSColor.whiteColor().setFill()
                AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                    AppKit.NSMakeRect(25.0, 22.0, 2.0, 2.0)
                ).fill()
            else:
                muted.setFill()
                AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                    AppKit.NSMakeRect(19.0, 21.0, 14.0, 14.0)
                ).fill()
                accent.setFill()
                AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                    AppKit.NSMakeRect(23.0, 25.0, 6.0, 6.0)
                ).fill()

            font = AppKit.NSFont.systemFontOfSize_weight_(
                14.0, AppKit.NSFontWeightSemibold
            )
            text_color = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                0.96, 0.97, 1.0, 1.0
            )
            attributes = {
                AppKit.NSFontAttributeName: font,
                AppKit.NSForegroundColorAttributeName: text_color,
            }
            text = AppKit.NSString.stringWithString_(state.label)
            text.drawInRect_withAttributes_(
                AppKit.NSMakeRect(
                    label_x,
                    (bounds.size.height - 20.0) / 2.0,
                    bounds.size.width - label_x - 16.0,
                    20.0,
                ),
                attributes,
            )

    _PILL_VIEW_CLASS = JisprRecordingPillView
    return _PILL_VIEW_CLASS


def _make_pulse_class(Foundation):
    global _PULSE_CLASS
    if _PULSE_CLASS is not None:
        return _PULSE_CLASS

    class JisprPillRunLoopPulse(Foundation.NSObject):
        def tick_(self, _timer):
            # Enter Python regularly so SIGINT is handled promptly while
            # NSApplication owns the main thread.
            return None

    _PULSE_CLASS = JisprPillRunLoopPulse
    return _PULSE_CLASS


class MacPillSurface:
    """A borderless bottom-center NSPanel that never takes keyboard focus."""

    def __init__(
        self,
        AppKit,
        objc,
        initial_view: PillView,
        style: str = "compact",
    ) -> None:
        self._AppKit = AppKit
        self._style = style
        self._last_kind = initial_view.kind
        rect = self._frame_for_view(initial_view)

        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setLevel_(AppKit.NSFloatingWindowLevel)
        panel.setIgnoresMouseEvents_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setReleasedWhenClosed_(False)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
        )

        width, height = _pill_layout(style, initial_view)
        view_class = _make_pill_view_class(AppKit, objc)
        view = view_class.alloc().initWithFrame_(
            AppKit.NSMakeRect(0.0, 0.0, width, height)
        )
        view.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        view.pill_view = initial_view
        view.pill_style = style
        panel.setContentView_(view)
        self._panel = panel
        self._view = view

    def _current_screen(self):
        screen = self._AppKit.NSScreen.mainScreen()
        if screen is None:
            screens = self._AppKit.NSScreen.screens()
            screen = screens[0] if screens else None
        if screen is None:
            raise LocalFlowError("No macOS display is available for the floating pill.")
        return screen

    def _frame_for_view(self, view: PillView):
        screen = self._current_screen()
        visible = screen.visibleFrame()
        width, height = _pill_layout(self._style, view)
        margin = 24.0 if self._style == "expanded" else _BOTTOM_MARGIN
        x = visible.origin.x + (visible.size.width - width) / 2.0
        y = visible.origin.y + margin
        return self._AppKit.NSMakeRect(x, y, width, height)

    def show(self) -> None:
        self._panel.orderFrontRegardless()

    def render(self, view: PillView) -> None:
        if view.kind != self._last_kind:
            self._panel.setFrame_display_animate_(
                self._frame_for_view(view),
                True,
                self._style == "compact",
            )
            self._last_kind = view.kind
        self._view.pill_view = view
        self._view.setNeedsDisplay_(True)

    def close(self) -> None:
        self._panel.orderOut_(None)
        self._panel.close()


class MacPillApplication:
    """Own AppKit on the main thread and run dictation on a worker thread."""

    def __init__(self, hotkey: str, style: str = "compact") -> None:
        if sys.platform != "darwin":
            raise LocalFlowError(
                "The floating recording pill is currently available only on macOS.",
                hint="Set LOCAL_FLOW_FLOATING_PILL=false on this platform.",
            )
        try:
            import AppKit
            import Foundation
            import objc
            from PyObjCTools import AppHelper
        except (ImportError, OSError) as exc:
            raise LocalFlowError(
                f"The macOS floating pill backend is unavailable: {exc}",
                hint="Install desktop extras: uv sync --extra desktop",
            ) from exc

        self._AppKit = AppKit
        self._Foundation = Foundation
        self._AppHelper = AppHelper
        try:
            if style not in ("compact", "expanded"):
                raise LocalFlowError(f"Unknown floating pill style: {style!r}.")
            self._app = AppKit.NSApplication.sharedApplication()
            self._app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
            # This is a CLI process rather than an app bundle launched by
            # LaunchServices, so AppKit will not finish launching on our
            # behalf before the first panel is ordered forward.
            self._app.finishLaunching()
            machine = PillStateMachine(hotkey)
            self.surface = MacPillSurface(AppKit, objc, machine.view, style=style)
        except LocalFlowError:
            raise
        except Exception as exc:
            raise LocalFlowError(
                f"The macOS floating pill could not be created: {exc}",
                hint="Use --no-pill to continue with console status.",
            ) from exc
        self.reporter = PillReporter(
            self.surface,
            machine,
            dispatch=AppHelper.callAfter,
            dispatch_later=lambda delay, action: AppHelper.callLater(delay, action),
        )

    def run(
        self,
        runner: Callable[[StatusReporter, threading.Event], int],
        console_reporter: StatusReporter,
    ) -> int:
        stop_event = threading.Event()
        result = [0]
        interrupted = [False]
        composite = CompositeReporter(console_reporter, self.reporter)

        def worker() -> None:
            try:
                result[0] = runner(composite, stop_event)
            except Exception as exc:
                result[0] = 1
                print(f"error: dictation loop stopped: {exc}", file=sys.stderr)
            finally:
                self._AppHelper.callAfter(self._AppHelper.stopEventLoop)

        def handle_interrupt(_signal_number, _frame) -> None:
            if interrupted[0]:
                self._AppHelper.stopEventLoop()
                return
            interrupted[0] = True
            stop_event.set()
            # The dictation worker now wakes its native hotkey loop and asks
            # AppKit to stop when cleanup is complete. Keep a bounded fallback
            # so a misbehaving third-party listener can never hang Ctrl+C.
            self._AppHelper.callLater(2.0, self._AppHelper.stopEventLoop)

        pulse_class = _make_pulse_class(self._Foundation)
        pulse = pulse_class.alloc().init()
        schedule_timer = (
            self._Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_
        )
        timer = schedule_timer(0.1, pulse, "tick:", None, True)
        previous_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, handle_interrupt)
        thread = threading.Thread(target=worker, daemon=True)

        self.surface.show()
        self.reporter.notify("idle")
        thread.start()
        try:
            self._AppHelper.runEventLoop(installInterrupt=False)
        finally:
            stop_event.set()
            thread.join(timeout=2.25)
            timer.invalidate()
            self.surface.close()
            signal.signal(signal.SIGINT, previous_handler)
        if interrupted[0]:
            print("\nbye")
        return result[0]
