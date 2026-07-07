"""Tests for the tray state machine (pure) and Pillow icon generation.

Icon tests are guarded by ``pytest.importorskip("PIL")`` so the suite still
passes in an environment without the ``tray`` extra installed.
"""

import pytest

from local_flow.tray.state import TrayStateMachine, TrayView


class TestTrayStateMachine:
    """Table-driven mapping from run-loop ``State`` to ``TrayView``."""

    @pytest.mark.parametrize(
        "state, detail, expected_icon, expected_flash",
        [
            ("idle", "", "idle", False),
            ("recording", "", "recording", False),
            ("processing", "", "processing", False),
            ("inserted", "hello world", "idle", True),
            ("error", "boom", "error", False),
            ("warning", "LM Studio polish skipped", "error", False),
            ("preview", "rough partial text", "processing", False),
        ],
    )
    def test_state_maps_to_icon_and_flash(self, state, detail, expected_icon, expected_flash):
        view = TrayStateMachine().apply(state, detail)
        assert isinstance(view, TrayView)
        assert view.icon == expected_icon
        assert view.flash is expected_flash

    def test_idle_tooltip(self):
        view = TrayStateMachine().apply("idle")
        assert view.tooltip == "local-flow — idle"

    def test_recording_tooltip(self):
        view = TrayStateMachine().apply("recording")
        assert view.tooltip == "local-flow — recording"

    def test_processing_tooltip(self):
        view = TrayStateMachine().apply("processing")
        assert view.tooltip == "local-flow — processing"

    def test_inserted_tooltip_shows_detail_prefix(self):
        view = TrayStateMachine().apply("inserted", "send the invoice")
        assert view.tooltip == "inserted: send the invoice"

    def test_inserted_tooltip_truncates_to_40_chars(self):
        long_text = "a" * 100
        view = TrayStateMachine().apply("inserted", long_text)
        assert view.tooltip == "inserted: " + "a" * 40
        assert len(view.tooltip) == len("inserted: ") + 40

    def test_error_tooltip_includes_detail(self):
        view = TrayStateMachine().apply("error", "Fake sink was configured to fail.")
        assert view.icon == "error"
        assert "Fake sink was configured to fail." in view.tooltip

    def test_warning_tooltip_includes_detail(self):
        view = TrayStateMachine().apply("warning", "LM Studio polish skipped")
        assert view.icon == "error"
        assert "LM Studio polish skipped" in view.tooltip

    def test_preview_tooltip_shows_detail_prefix(self):
        view = TrayStateMachine().apply("preview", "rough partial text")
        assert view.icon == "processing"
        assert view.tooltip == "… rough partial text"
        assert view.flash is False

    def test_preview_tooltip_truncates_to_40_chars(self):
        long_text = "a" * 100
        view = TrayStateMachine().apply("preview", long_text)
        assert view.tooltip == "… " + "a" * 40

    def test_warning_then_next_state_does_not_stick_on_error(self):
        machine = TrayStateMachine()
        warning_view = machine.apply("warning", "LM Studio polish skipped")
        assert warning_view.icon == "error"

        recording_view = machine.apply("recording")
        assert recording_view.icon == "recording"
        assert recording_view.flash is False

    def test_warning_then_idle_returns_idle_view(self):
        machine = TrayStateMachine()
        machine.apply("warning", "boom")
        idle_view = machine.apply("idle")
        assert idle_view == TrayView(icon="idle", tooltip="local-flow — idle")


class TestDrawIcon:
    """Pillow-based icon rendering; skipped without the ``tray`` extra."""

    @pytest.fixture(autouse=True)
    def _require_pillow(self):
        pytest.importorskip("PIL")

    def test_returns_rgba_image_of_requested_size(self):
        from local_flow.tray.icons import draw_icon

        image = draw_icon("idle", size=64)
        assert image.size == (64, 64)
        assert image.mode == "RGBA"

    def test_default_size_is_64(self):
        from local_flow.tray.icons import draw_icon

        image = draw_icon("idle")
        assert image.size == (64, 64)

    def test_custom_size_is_respected(self):
        from local_flow.tray.icons import draw_icon

        image = draw_icon("recording", size=32)
        assert image.size == (32, 32)

    @staticmethod
    def _dominant_opaque_color(image):
        """Most common fully-opaque pixel color (the circle fill)."""
        colors = image.getcolors(maxcolors=image.size[0] * image.size[1])
        opaque = [(count, color) for count, color in colors if color[3] == 255]
        opaque.sort(key=lambda pair: pair[0], reverse=True)
        return opaque[0][1][:3]

    @pytest.mark.parametrize("kind", ["idle", "recording", "processing", "error"])
    def test_each_kind_renders_an_rgba_image(self, kind):
        from local_flow.tray.icons import draw_icon

        image = draw_icon(kind)
        assert image.size == (64, 64)
        assert image.mode == "RGBA"

    def test_kinds_have_distinct_dominant_colors(self):
        from local_flow.tray.icons import draw_icon

        colors = {
            kind: self._dominant_opaque_color(draw_icon(kind))
            for kind in ("idle", "recording", "processing", "error")
        }
        assert len(set(colors.values())) == 4

    def test_unknown_kind_falls_back_to_idle_color(self):
        from local_flow.tray.icons import draw_icon

        idle_color = self._dominant_opaque_color(draw_icon("idle"))
        unknown_color = self._dominant_opaque_color(draw_icon("totally-not-a-state"))
        assert unknown_color == idle_color

    def test_error_icon_has_white_pixels_for_exclamation_mark(self):
        from local_flow.tray.icons import draw_icon

        image = draw_icon("error")
        colors = image.getcolors(maxcolors=64 * 64)
        assert any(color[:3] == (255, 255, 255) and color[3] == 255 for _, color in colors)

    def test_non_error_icons_have_no_white_exclamation_pixels(self):
        from local_flow.tray.icons import draw_icon

        image = draw_icon("idle")
        colors = image.getcolors(maxcolors=64 * 64)
        assert not any(color[:3] == (255, 255, 255) and color[3] == 255 for _, color in colors)
