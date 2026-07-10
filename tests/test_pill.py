"""Headless contracts for the floating recording pill."""

from __future__ import annotations

import array
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

import local_flow.app as app_module
import local_flow.pill.macos as macos_module
from local_flow.app import _cmd_run
from local_flow.audio.level import pcm_level
from local_flow.config import load_config
from local_flow.errors import LocalFlowError
from local_flow.pill.reporter import PillReporter
from local_flow.pill.state import PillStateMachine, PillView
from local_flow.status import CompositeReporter, StatusReporter


class FakeSurface:
    def __init__(self):
        self.views = []

    def render(self, view):
        self.views.append(view)


class CollectingReporter(StatusReporter):
    def __init__(self, wants_level=False):
        self.wants_audio_level = wants_level
        self.events = []
        self.levels = []

    def notify(self, state, detail=""):
        self.events.append((state, detail))

    def audio_level(self, level):
        self.levels.append(level)


class TestPcmLevel:
    def test_empty_and_silent_pcm_are_zero(self):
        assert pcm_level(b"") == 0.0
        assert pcm_level(b"\x00\x00" * 480) == 0.0

    def test_full_scale_pcm_reaches_the_top_of_the_meter(self):
        pcm = array.array("h", [32767] * 480).tobytes()
        assert pcm_level(pcm) == pytest.approx(1.0, abs=0.001)

    def test_odd_trailing_byte_is_ignored(self):
        pcm = array.array("h", [16000] * 20).tobytes()
        assert pcm_level(pcm + b"x") == pcm_level(pcm)


class TestPillStateMachine:
    @pytest.mark.parametrize(
        ("state", "kind", "label", "meter"),
        [
            ("idle", "idle", "Ready · Hold Fn", False),
            ("recording", "recording", "Listening", True),
            ("processing", "processing", "Transcribing…", False),
            ("preview", "processing", "Transcribing…", False),
            ("inserted", "inserted", "Inserted", False),
        ],
    )
    def test_status_mapping(self, state, kind, label, meter):
        view = PillStateMachine("fn").apply(state)
        assert view == PillView(kind, label, show_meter=meter)

    def test_idle_label_uses_configured_hotkey(self):
        assert PillStateMachine("f9").view.label == "Ready · Hold F9"

    def test_warning_uses_truncated_detail(self):
        view = PillStateMachine().apply("warning", "x" * 100)
        assert view.kind == "error"
        assert view.label == "x" * 34

    def test_level_is_clamped_and_only_changes_recording_view(self):
        machine = PillStateMachine()
        assert machine.set_level(0.8).level == 0.0
        machine.apply("recording")
        assert machine.set_level(2.0).level == 1.0
        assert machine.set_level(-1.0).level == 0.0


class TestPillLayout:
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("idle", (72.0, 8.0)),
            ("recording", (104.0, 20.0)),
            ("processing", (104.0, 20.0)),
            ("inserted", (104.0, 20.0)),
            ("error", (240.0, 36.0)),
        ],
    )
    def test_compact_size_tracks_state(self, kind, expected):
        assert macos_module._pill_layout("compact", PillView(kind, "status")) == expected

    @pytest.mark.parametrize(
        "kind", ["idle", "recording", "processing", "inserted", "error"]
    )
    def test_expanded_size_stays_labeled_pill(self, kind):
        assert macos_module._pill_layout(
            "expanded", PillView(kind, "status")
        ) == (280.0, 56.0)


class TestPillReporter:
    def test_status_and_throttled_levels_dispatch_to_surface(self):
        times = iter([0.0, 0.01, 0.06])
        surface = FakeSurface()
        pending = []
        reporter = PillReporter(
            surface,
            dispatch=pending.append,
            clock=lambda: next(times),
            max_level_fps=20,
        )

        reporter.notify("recording")
        reporter.audio_level(0.2)
        reporter.audio_level(0.8)  # within 50 ms: dropped
        reporter.audio_level(0.9)
        for action in pending:
            action()

        assert [view.level for view in surface.views] == [0.0, 0.2, 0.9]

    def test_levels_while_idle_do_not_render(self):
        surface = FakeSurface()
        reporter = PillReporter(surface, clock=lambda: 0.0)
        reporter.audio_level(0.9)
        assert surface.views == []

    def test_surface_failure_never_escapes(self):
        class BrokenSurface:
            def render(self, _view):
                raise RuntimeError("display disconnected")

        PillReporter(BrokenSurface()).notify("recording")

    def test_inserted_state_flashes_before_delayed_idle(self):
        surface = FakeSurface()
        delayed = []
        reporter = PillReporter(
            surface,
            dispatch_later=lambda delay, action: delayed.append((delay, action)),
        )

        reporter.notify("inserted")
        reporter.notify("idle")

        assert [view.kind for view in surface.views] == ["inserted"]
        assert delayed[0][0] == 0.8
        delayed[0][1]()
        assert [view.kind for view in surface.views] == ["inserted", "idle"]

    def test_new_recording_cancels_stale_delayed_idle(self):
        surface = FakeSurface()
        delayed = []
        reporter = PillReporter(
            surface,
            dispatch_later=lambda delay, action: delayed.append(action),
        )
        reporter.notify("inserted")
        reporter.notify("idle")
        reporter.notify("recording")

        delayed[0]()

        assert [view.kind for view in surface.views] == ["inserted", "recording"]


def test_composite_reporter_only_forwards_levels_to_interested_reporters():
    console = CollectingReporter(wants_level=False)
    pill = CollectingReporter(wants_level=True)
    reporter = CompositeReporter(console, pill)

    reporter.notify("recording")
    reporter.audio_level(0.75)

    assert reporter.wants_audio_level is True
    assert console.events == pill.events == [("recording", "")]
    assert console.levels == []
    assert pill.levels == [0.75]


class TestRunWithPill:
    def test_enabled_pill_wraps_the_existing_run_loop(self, monkeypatch):
        calls = []

        class FakeMacPillApplication:
            def __init__(self, hotkey, style):
                calls.append(("pill", hotkey, style))

            def run(self, runner, console):
                calls.append(("console", type(console).__name__))
                return runner(CollectingReporter(), threading.Event())

        def fake_run_loop(config, mode, reporter, stop_event=None):
            calls.append(("run", mode, stop_event is not None))
            return 7

        monkeypatch.setattr(macos_module, "MacPillApplication", FakeMacPillApplication)
        monkeypatch.setattr(app_module, "_run_loop", fake_run_loop)
        config = replace(
            load_config(env={}),
            floating_pill=True,
            hotkey="fn",
            pill_style="compact",
        )

        code = _cmd_run(SimpleNamespace(mode=None, pill=None), config)

        assert code == 7
        assert calls == [
            ("pill", "fn", "compact"),
            ("console", "ConsoleReporter"),
            ("run", "push-to-talk", True),
        ]

    def test_no_pill_override_runs_console_directly(self, monkeypatch):
        calls = []

        def fake_run_loop(config, mode, reporter, stop_event=None):
            calls.append((mode, type(reporter).__name__, stop_event))
            return 3

        monkeypatch.setattr(app_module, "_run_loop", fake_run_loop)
        config = replace(load_config(env={}), floating_pill=True)

        code = _cmd_run(SimpleNamespace(mode=None, pill=False), config)

        assert code == 3
        assert calls == [("push-to-talk", "ConsoleReporter", None)]

    def test_unavailable_pill_warns_and_falls_back(self, monkeypatch, capsys):
        class MissingPill:
            def __init__(self, _hotkey, style):
                raise LocalFlowError("AppKit missing", hint="install desktop extras")

        monkeypatch.setattr(macos_module, "MacPillApplication", MissingPill)
        monkeypatch.setattr(app_module, "_run_loop", lambda *_args, **_kwargs: 0)
        config = replace(load_config(env={}), floating_pill=True)

        assert _cmd_run(SimpleNamespace(mode=None, pill=None), config) == 0
        error = capsys.readouterr().err
        assert "floating pill unavailable" in error
        assert "AppKit missing" in error
        assert "install desktop extras" in error
