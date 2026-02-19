"""Tests for utility modules: text_utils, layout_utils, image_utils."""

import pytest
from PIL import Image, ImageDraw, ImageFont
from unittest.mock import patch, MagicMock

from utils.app_utils import get_font
from utils.text_utils import (
    wrap_text, truncate_text, get_text_dimensions,
    draw_multiline_text, measure_text_block,
)
from utils.layout_utils import (
    calculate_grid, draw_rounded_rect, draw_progress_bar, draw_dotted_rect,
)
from utils.image_utils import (
    resize_image, change_orientation, apply_image_enhancement, compute_image_hash,
)
from utils.time_utils import calculate_seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def draw():
    """ImageDraw on a 800x480 white canvas."""
    img = Image.new("RGB", (800, 480), "white")
    return ImageDraw.Draw(img)


@pytest.fixture
def font():
    """Jost font at a reasonable test size."""
    f = get_font("Jost", 20)
    assert f is not None, "Jost font not found — check static/fonts"
    return f


# ===========================================================================
# text_utils
# ===========================================================================

class TestWrapText:
    def test_empty_string(self, draw, font):
        assert wrap_text(draw, "", font, 400) == []

    def test_short_string_fits(self, draw, font):
        lines = wrap_text(draw, "Hello", font, 400)
        assert lines == ["Hello"]

    def test_long_string_wraps(self, draw, font):
        text = "The quick brown fox jumps over the lazy dog near the river"
        lines = wrap_text(draw, text, font, 200)
        assert len(lines) >= 2
        # All words present when joined
        assert " ".join(lines) == text

    def test_single_long_word(self, draw, font):
        word = "Supercalifragilisticexpialidocious"
        lines = wrap_text(draw, word, font, 50)
        # Single word exceeding max_width kept intact
        assert lines == [word]


class TestTruncateText:
    def test_empty_string(self, draw, font):
        assert truncate_text(draw, "", font, 400) == ""

    def test_short_fits(self, draw, font):
        assert truncate_text(draw, "Hi", font, 400) == "Hi"

    def test_long_gets_truncated(self, draw, font):
        text = "A" * 200
        result = truncate_text(draw, text, font, 100)
        assert result.endswith("...")
        assert len(result) < len(text)

    def test_custom_suffix(self, draw, font):
        text = "A" * 200
        result = truncate_text(draw, text, font, 100, suffix="~")
        assert result.endswith("~")


class TestGetTextDimensions:
    def test_returns_tuple(self, draw, font):
        w, h = get_text_dimensions(draw, "Hello", font)
        assert isinstance(w, (int, float))
        assert isinstance(h, (int, float))
        assert w > 0
        assert h > 0

    def test_longer_string_wider(self, draw, font):
        w1, _ = get_text_dimensions(draw, "A", font)
        w2, _ = get_text_dimensions(draw, "AAAA", font)
        assert w2 > w1


class TestDrawMultilineText:
    def test_returns_positive_height(self, draw, font):
        h = draw_multiline_text(
            draw, "Hello world this is a test", (10, 10), font, "black", 200
        )
        assert h > 0

    def test_alignment_center(self, draw, font):
        h = draw_multiline_text(
            draw, "Centered text", (10, 10), font, "black", 200, align="center"
        )
        assert h > 0

    def test_alignment_right(self, draw, font):
        h = draw_multiline_text(
            draw, "Right text", (10, 10), font, "black", 200, align="right"
        )
        assert h > 0


class TestMeasureTextBlock:
    def test_empty(self, draw, font):
        assert measure_text_block(draw, "", font, 400) == 0

    def test_matches_draw_height(self, draw, font):
        text = "Hello world testing measure"
        measured = measure_text_block(draw, text, font, 200)
        drawn = draw_multiline_text(
            draw, text, (0, 0), font, "black", 200, line_spacing=4
        )
        # Measured and drawn should be very close (drawn includes trailing spacing)
        assert abs(measured - drawn) <= 4  # within one line_spacing


# ===========================================================================
# layout_utils
# ===========================================================================

class TestCalculateGrid:
    def test_single_cell(self):
        cells = calculate_grid((0, 0, 800, 480), 1, 1)
        assert len(cells) == 1
        assert cells[0] == (0, 0, 800, 480)

    def test_2x2_no_spacing(self):
        cells = calculate_grid((0, 0, 400, 400), 2, 2)
        assert len(cells) == 4
        assert cells[0] == (0, 0, 200, 200)
        assert cells[3] == (200, 200, 200, 200)

    def test_with_spacing(self):
        cells = calculate_grid((0, 0, 100, 100), 2, 2, spacing=10)
        assert len(cells) == 4
        # Each cell: (100 - 10) // 2 = 45
        assert cells[0][2] == 45
        assert cells[0][3] == 45

    def test_3x1(self):
        cells = calculate_grid((10, 10, 300, 100), 1, 3)
        assert len(cells) == 3


class TestDrawRoundedRect:
    def test_draws_without_error(self):
        img = Image.new("RGB", (200, 200), "white")
        d = ImageDraw.Draw(img)
        draw_rounded_rect(d, (10, 10, 190, 190), 15, fill="blue")

    def test_zero_radius_fallback(self):
        img = Image.new("RGB", (200, 200), "white")
        d = ImageDraw.Draw(img)
        draw_rounded_rect(d, (10, 10, 190, 190), 0, fill="red")

    def test_oversized_radius_clamped(self):
        img = Image.new("RGB", (50, 50), "white")
        d = ImageDraw.Draw(img)
        # Radius 100 clamped to half smallest dimension (25)
        draw_rounded_rect(d, (0, 0, 50, 50), 100, fill="green")


class TestDrawProgressBar:
    def test_zero_progress(self):
        img = Image.new("RGB", (300, 50), "white")
        d = ImageDraw.Draw(img)
        draw_progress_bar(d, (10, 10), (280, 30), 0.0, "blue", "gray")

    def test_full_progress(self):
        img = Image.new("RGB", (300, 50), "white")
        d = ImageDraw.Draw(img)
        draw_progress_bar(d, (10, 10), (280, 30), 1.0, "green", "gray")

    def test_clamps_over_one(self):
        img = Image.new("RGB", (300, 50), "white")
        d = ImageDraw.Draw(img)
        draw_progress_bar(d, (10, 10), (280, 30), 1.5, "blue", "gray")

    def test_with_border_and_radius(self):
        img = Image.new("RGB", (300, 50), "white")
        d = ImageDraw.Draw(img)
        draw_progress_bar(
            d, (10, 10), (280, 30), 0.5, "blue", "gray",
            border_color="black", border_width=2, radius=10
        )


class TestDrawDottedRect:
    def test_draws_without_error(self):
        img = Image.new("RGB", (200, 200), "white")
        d = ImageDraw.Draw(img)
        draw_dotted_rect(d, (10, 10, 190, 190), "black")


# ===========================================================================
# image_utils
# ===========================================================================

class TestResizeImage:
    def test_basic_resize(self):
        img = Image.new("RGB", (1600, 900))
        result = resize_image(img, (800, 480))
        assert result.size == (800, 480)

    def test_already_correct_size(self):
        img = Image.new("RGB", (800, 480))
        result = resize_image(img, (800, 480))
        assert result.size == (800, 480)

    def test_portrait_to_landscape(self):
        img = Image.new("RGB", (480, 800))
        result = resize_image(img, (800, 480))
        assert result.size == (800, 480)

    def test_keep_width_setting(self):
        img = Image.new("RGB", (1600, 900))
        result = resize_image(img, (800, 480), image_settings=["keep-width"])
        assert result.size == (800, 480)


class TestChangeOrientation:
    def test_horizontal_no_change(self):
        img = Image.new("RGB", (800, 480))
        result = change_orientation(img, "horizontal")
        assert result.size == (800, 480)

    def test_vertical_rotates(self):
        img = Image.new("RGB", (800, 480))
        result = change_orientation(img, "vertical")
        assert result.size == (480, 800)

    def test_inverted(self):
        img = Image.new("RGB", (800, 480))
        result = change_orientation(img, "horizontal", inverted=True)
        assert result.size == (800, 480)

    def test_vertical_inverted(self):
        img = Image.new("RGB", (800, 480))
        result = change_orientation(img, "vertical", inverted=True)
        # 90 + 180 = 270 degrees
        assert result.size == (480, 800)


class TestApplyImageEnhancement:
    def test_default_no_change(self):
        img = Image.new("RGB", (100, 100), "red")
        result = apply_image_enhancement(img)
        assert result.size == (100, 100)

    def test_custom_settings(self):
        img = Image.new("RGB", (100, 100), "red")
        settings = {
            "brightness": 1.5,
            "contrast": 0.8,
            "saturation": 1.2,
            "sharpness": 1.1,
        }
        result = apply_image_enhancement(img, settings)
        assert result.size == (100, 100)

    def test_rgba_converted(self):
        img = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
        result = apply_image_enhancement(img)
        assert result.mode == "RGB"


class TestComputeImageHash:
    def test_returns_hex_string(self):
        img = Image.new("RGB", (10, 10), "red")
        h = compute_image_hash(img)
        assert isinstance(h, str)
        assert len(h) == 8
        int(h, 16)  # should parse as hex

    def test_same_image_same_hash(self):
        img1 = Image.new("RGB", (10, 10), "blue")
        img2 = Image.new("RGB", (10, 10), "blue")
        assert compute_image_hash(img1) == compute_image_hash(img2)

    def test_different_image_different_hash(self):
        img1 = Image.new("RGB", (10, 10), "red")
        img2 = Image.new("RGB", (10, 10), "blue")
        assert compute_image_hash(img1) != compute_image_hash(img2)


# ===========================================================================
# time_utils
# ===========================================================================

class TestCalculateSeconds:
    def test_minutes(self):
        assert calculate_seconds(5, "minute") == 300

    def test_hours(self):
        assert calculate_seconds(2, "hour") == 7200

    def test_days(self):
        assert calculate_seconds(1, "day") == 86400

    def test_unknown_unit_default(self):
        assert calculate_seconds(99, "parsec") == 300
