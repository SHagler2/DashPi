"""Tests for no-dependency plugins: Clock, Countdown, YearProgress, TodoList.

Each plugin is instantiated with a config dict and called with mock_device_config.
All external time is frozen via pytz mocking where needed.
"""

import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from PIL import Image


def assert_valid_image(img, expected_size=None):
    """Assert that img is a valid PIL Image, optionally checking dimensions."""
    assert isinstance(img, Image.Image), f"Expected PIL Image, got {type(img)}"
    assert img.size[0] > 0 and img.size[1] > 0, "Image has zero dimensions"
    if expected_size:
        assert img.size == expected_size, f"Expected {expected_size}, got {img.size}"

# Plugin configs matching plugin-info.json
CLOCK_CONFIG = {"id": "clock", "display_name": "Clock", "class": "Clock"}
COUNTDOWN_CONFIG = {"id": "countdown", "display_name": "Countdown", "class": "Countdown"}
YEAR_PROGRESS_CONFIG = {"id": "year_progress", "display_name": "Year Progress", "class": "YearProgress"}
TODO_LIST_CONFIG = {"id": "todo_list", "display_name": "To-Do List", "class": "TodoList"}


# ===========================================================================
# Clock Plugin
# ===========================================================================

class TestClock:
    @pytest.fixture
    def clock(self):
        from plugins.clock.clock import Clock
        return Clock(CLOCK_CONFIG)

    @pytest.mark.parametrize("face", [
        "Gradient Clock", "Digital Clock", "Divided Clock", "Word Clock"
    ])
    def test_all_faces(self, clock, mock_device_config, face):
        settings = {"selectedClockFace": face}
        img = clock.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_default_face_on_invalid(self, clock, mock_device_config):
        settings = {"selectedClockFace": "Nonexistent"}
        img = clock.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_vertical_orientation(self, clock, mock_device_config):
        mock_device_config.get_config.side_effect = lambda key=None, default=None: (
            "vertical" if key == "orientation"
            else "US/Central" if key == "timezone"
            else {} if key is not None else {}
        )
        settings = {"selectedClockFace": "Digital Clock"}
        img = clock.generate_image(settings, mock_device_config)
        assert_valid_image(img, (480, 800))

    def test_custom_colors(self, clock, mock_device_config):
        settings = {
            "selectedClockFace": "Gradient Clock",
            "primaryColor": "#ff0000",
            "secondaryColor": "#0000ff",
        }
        img = clock.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_format_time_no_pad(self):
        from plugins.clock.clock import Clock
        assert Clock.format_time(9, 5) == "9:5"

    def test_format_time_zero_pad(self):
        from plugins.clock.clock import Clock
        assert Clock.format_time(9, 5, zero_pad=True) == "09:05"

    def test_calculate_clock_angles(self):
        from plugins.clock.clock import Clock
        dt = datetime(2024, 1, 1, 12, 0, 0)
        hour_angle, minute_angle = Clock.calculate_clock_angles(dt)
        # At 12:00, both hands point up (90 degrees in clock convention)
        import math
        assert abs(hour_angle - math.radians(90)) < 0.01
        assert abs(minute_angle - math.radians(90)) < 0.01

    def test_word_clock_positions_oclock(self):
        from plugins.clock.clock import Clock
        positions = Clock.translate_word_grid_positions(3, 0)
        # Should contain IT IS and OCLOCK
        assert [0, 0] in positions  # I
        assert [0, 1] in positions  # T
        assert [9, 5] in positions  # O (OCLOCK)

    def test_word_clock_positions_past(self):
        from plugins.clock.clock import Clock
        positions = Clock.translate_word_grid_positions(3, 15)
        # Should contain IT IS, QUARTER, PAST
        assert [4, 0] in positions  # P (PAST)

    def test_word_clock_positions_to(self):
        from plugins.clock.clock import Clock
        positions = Clock.translate_word_grid_positions(3, 45)
        # Should contain IT IS, QUARTER, TO
        assert [3, 9] in positions  # T (TO)


# ===========================================================================
# Countdown Plugin
# ===========================================================================

class TestCountdown:
    @pytest.fixture
    def countdown(self):
        from plugins.countdown.countdown import Countdown
        return Countdown(COUNTDOWN_CONFIG)

    def test_future_date(self, countdown, mock_device_config):
        settings = {"title": "New Year", "date": "2099-01-01"}
        img = countdown.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_past_date(self, countdown, mock_device_config):
        settings = {"title": "Past Event", "date": "2020-01-01"}
        img = countdown.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_no_title(self, countdown, mock_device_config):
        settings = {"date": "2099-06-15"}
        img = countdown.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_missing_date_raises(self, countdown, mock_device_config):
        with pytest.raises(RuntimeError, match="Date is required"):
            countdown.generate_image({}, mock_device_config)

    def test_render_pil_directly(self, countdown):
        img = countdown._render_pil(
            (800, 480), "Test", "January 01, 2099", 100, "Days Left",
            {"backgroundColor": "#ffffff", "textColor": "#000000"}
        )
        assert_valid_image(img, (800, 480))

    def test_custom_colors(self, countdown, mock_device_config):
        settings = {
            "title": "Colorful",
            "date": "2099-01-01",
            "backgroundColor": "#000000",
            "textColor": "#ffffff",
        }
        img = countdown.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))


# ===========================================================================
# YearProgress Plugin
# ===========================================================================

class TestYearProgress:
    @pytest.fixture
    def year_progress(self):
        from plugins.year_progress.year_progress import YearProgress
        return YearProgress(YEAR_PROGRESS_CONFIG)

    def test_generates_image(self, year_progress, mock_device_config):
        img = year_progress.generate_image({}, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_render_pil_zero_percent(self, year_progress):
        img = year_progress._render_pil(
            (800, 480), 2024, 0, 365,
            {"backgroundColor": "#ffffff", "textColor": "#000000"}
        )
        assert_valid_image(img, (800, 480))

    def test_render_pil_fifty_percent(self, year_progress):
        img = year_progress._render_pil(
            (800, 480), 2024, 50, 183,
            {"backgroundColor": "#ffffff", "textColor": "#000000"}
        )
        assert_valid_image(img, (800, 480))

    def test_render_pil_hundred_percent(self, year_progress):
        img = year_progress._render_pil(
            (800, 480), 2024, 100, 0,
            {"backgroundColor": "#ffffff", "textColor": "#000000"}
        )
        assert_valid_image(img, (800, 480))

    def test_custom_colors(self, year_progress):
        img = year_progress._render_pil(
            (800, 480), 2024, 42, 211,
            {"backgroundColor": "#000000", "textColor": "#00ff00"}
        )
        assert_valid_image(img, (800, 480))


# ===========================================================================
# TodoList Plugin
# ===========================================================================

class TestTodoList:
    @pytest.fixture
    def todo_list(self):
        from plugins.todo_list.todo_list import TodoList
        return TodoList(TODO_LIST_CONFIG)

    def _make_settings(self, lists=None, font_size="normal", list_style="disc",
                       title=None):
        if lists is None:
            lists = [("Groceries", "Milk\nEggs\nBread")]
        return {
            "list-title[]": [t for t, _ in lists],
            "list[]": [items for _, items in lists],
            "fontSize": font_size,
            "listStyle": list_style,
            "title": title,
        }

    def test_single_list(self, todo_list, mock_device_config):
        settings = self._make_settings()
        img = todo_list.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_multiple_lists(self, todo_list, mock_device_config):
        settings = self._make_settings([
            ("Shopping", "Milk\nEggs"),
            ("Tasks", "Clean\nCode\nSleep"),
        ])
        img = todo_list.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_with_main_title(self, todo_list, mock_device_config):
        settings = self._make_settings(title="My Lists")
        img = todo_list.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    @pytest.mark.parametrize("font_size", ["x-small", "small", "normal", "large", "x-large"])
    def test_font_sizes(self, todo_list, mock_device_config, font_size):
        settings = self._make_settings(font_size=font_size)
        img = todo_list.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    @pytest.mark.parametrize("style", ["disc", "checkbox", "checkbox-checked", "decimal"])
    def test_list_styles(self, todo_list, mock_device_config, style):
        settings = self._make_settings(list_style=style)
        img = todo_list.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_many_items_truncation(self, todo_list, mock_device_config):
        long_list = "\n".join([f"Item {i}" for i in range(50)])
        settings = self._make_settings([("Long List", long_list)])
        img = todo_list.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_render_pil_directly(self, todo_list):
        lists = [{"title": "Test", "elements": ["A", "B", "C"]}]
        img = todo_list._render_pil(
            (800, 480), "Main Title", lists, "disc", 1.0,
            {"backgroundColor": "#ffffff", "textColor": "#000000"}
        )
        assert_valid_image(img, (800, 480))
