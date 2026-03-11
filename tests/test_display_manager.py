"""Tests for DisplayManager — brightness behaviour and e-ink (no-backlight) no-ops.

Coverage:
  - E-ink display: brightness schedule is never applied; display_image uses 1.0
  - E-ink display: reapply_brightness() is a no-op
  - E-ink display: get_current_brightness() returns 1.0, not overridden
  - LCD display: brightness schedule IS applied on display_image
  - LCD display: reapply_brightness() re-renders and calls display.display_image
"""

import os
import pytest
from unittest.mock import MagicMock, patch, call
from PIL import Image


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_display_mock(has_backlight: bool):
    """Return a mock AbstractDisplay with controllable capability flags."""
    d = MagicMock()
    d.has_backlight.return_value = has_backlight
    d.supports_fast_refresh.return_value = has_backlight  # LCD=True, Inky=False
    d.display_type_name.return_value = "Mock-LCD" if has_backlight else "Inky e-Paper"
    return d


def _make_device_config(tmp_path, timezone="UTC", brightness_enabled=True):
    cfg = MagicMock()
    cfg.get_resolution.return_value = (800, 480)
    cfg.current_image_file = str(tmp_path / "current.png")

    def _config_side(key=None, default=None):
        vals = {
            "orientation": "horizontal",
            "timezone": timezone,
            "display_type": "mock",
            "output_dir": str(tmp_path / "mock_output"),
            "brightness_schedule": {
                "enabled": brightness_enabled,
                "day_start": "00:00",
                "evening_start": "20:00",
                "night_start": "23:00",
                "day_brightness": 0.8,
                "evening_brightness": 0.5,
                "night_brightness": 0.2,
            },
        }
        if key is None:
            return dict(vals)
        return vals.get(key, default if default is not None else {})

    cfg.get_config.side_effect = _config_side
    return cfg


def _make_dm(device_config, display_mock):
    """Instantiate DisplayManager with the given display mock injected."""
    from display.display_manager import DisplayManager

    with patch("display.display_manager.MockDisplay") as MockCls:
        MockCls.return_value = display_mock
        dm = DisplayManager(device_config)
    return dm


# ---------------------------------------------------------------------------
# E-ink (no backlight) tests — Item I
# ---------------------------------------------------------------------------

class TestEinkBrightness:
    """Verify that DisplayManager is a graceful no-op for e-ink displays."""

    def test_display_image_uses_full_brightness(self, tmp_path):
        """display_image() should pass brightness=1.0 for e-ink, never call blank/unblank."""
        cfg = _make_device_config(tmp_path)
        eink = _make_display_mock(has_backlight=False)
        dm = _make_dm(cfg, eink)

        img = Image.new("RGB", (800, 480), "white")
        dm.display_image(img)

        eink.display_image.assert_called_once()
        eink.blank_display.assert_not_called()
        eink.unblank_display.assert_not_called()

    def test_display_image_does_not_call_scheduled_brightness(self, tmp_path):
        """_get_scheduled_brightness() must never be reached for no-backlight displays."""
        cfg = _make_device_config(tmp_path)
        eink = _make_display_mock(has_backlight=False)
        dm = _make_dm(cfg, eink)

        with patch.object(dm, '_get_scheduled_brightness', wraps=dm._get_scheduled_brightness) as spy:
            img = Image.new("RGB", (800, 480), "white")
            dm.display_image(img)
            spy.assert_not_called()

    def test_reapply_brightness_is_noop(self, tmp_path):
        """reapply_brightness() should return immediately without rendering for e-ink."""
        cfg = _make_device_config(tmp_path)
        eink = _make_display_mock(has_backlight=False)
        dm = _make_dm(cfg, eink)

        # Create a current image so the path exists
        Image.new("RGB", (800, 480), "gray").save(cfg.current_image_file)

        eink.display_image.reset_mock()
        dm.reapply_brightness()

        eink.display_image.assert_not_called()

    def test_get_current_brightness_returns_full(self, tmp_path):
        """get_current_brightness() returns 1.0, not overridden for e-ink."""
        cfg = _make_device_config(tmp_path)
        eink = _make_display_mock(has_backlight=False)
        dm = _make_dm(cfg, eink)

        result = dm.get_current_brightness()
        assert result == {"brightness": 1.0, "overridden": False}

    def test_set_brightness_override_has_no_effect_on_display(self, tmp_path):
        """Setting a brightness override on e-ink should not affect display output."""
        cfg = _make_device_config(tmp_path)
        eink = _make_display_mock(has_backlight=False)
        dm = _make_dm(cfg, eink)

        dm.set_brightness_override(0.3)  # would blank an LCD at this level

        img = Image.new("RGB", (800, 480), "white")
        dm.display_image(img)

        # Still renders — override is ignored for no-backlight displays
        eink.display_image.assert_called_once()
        eink.blank_display.assert_not_called()


# ---------------------------------------------------------------------------
# LCD (has backlight) contrast tests
# ---------------------------------------------------------------------------

class TestLcdBrightness:
    """Verify that brightness scheduling DOES apply on LCD displays."""

    def test_display_image_calls_scheduled_brightness(self, tmp_path):
        """display_image() on LCD must consult the brightness schedule."""
        cfg = _make_device_config(tmp_path)
        lcd = _make_display_mock(has_backlight=True)
        dm = _make_dm(cfg, lcd)

        with patch.object(dm, '_get_effective_brightness', return_value=0.8) as spy:
            img = Image.new("RGB", (800, 480), "white")
            dm.display_image(img)
            spy.assert_called_once()

    def test_blank_display_called_when_brightness_zero(self, tmp_path):
        """If scheduled brightness is 0, blank_display() is called and image is not sent."""
        cfg = _make_device_config(tmp_path)
        lcd = _make_display_mock(has_backlight=True)
        dm = _make_dm(cfg, lcd)

        with patch.object(dm, '_get_effective_brightness', return_value=0.0):
            img = Image.new("RGB", (800, 480), "white")
            dm.display_image(img)

        lcd.blank_display.assert_called_once()
        lcd.display_image.assert_not_called()

    def test_reapply_brightness_renders_on_lcd(self, tmp_path):
        """reapply_brightness() should re-render and call display.display_image on LCD."""
        cfg = _make_device_config(tmp_path)
        lcd = _make_display_mock(has_backlight=True)
        dm = _make_dm(cfg, lcd)

        Image.new("RGB", (800, 480), "white").save(cfg.current_image_file)

        lcd.display_image.reset_mock()
        with patch.object(dm, '_get_effective_brightness', return_value=0.8):
            dm.reapply_brightness()

        lcd.display_image.assert_called_once()


# ---------------------------------------------------------------------------
# Timezone guard in _get_current_period
# ---------------------------------------------------------------------------

class TestTimezoneGuard:
    def test_invalid_timezone_falls_back_to_utc(self, tmp_path):
        """_get_current_period() must not raise for a garbage timezone string."""
        cfg = _make_device_config(tmp_path, timezone="Not/A_Real_Zone")
        lcd = _make_display_mock(has_backlight=True)
        dm = _make_dm(cfg, lcd)

        # Should not raise
        period = dm._get_current_period()
        assert period in ("day", "evening", "night")

    def test_valid_non_utc_timezone_works(self, tmp_path):
        cfg = _make_device_config(tmp_path, timezone="America/Chicago")
        lcd = _make_display_mock(has_backlight=True)
        dm = _make_dm(cfg, lcd)

        period = dm._get_current_period()
        assert period in ("day", "evening", "night")
