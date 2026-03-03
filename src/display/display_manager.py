"""Display manager — auto-detects hardware and delegates rendering.

Probes for Inky (I2C), LCD (/dev/fb0 + sysfs), or Waveshare e-paper on
startup, then routes all display_image() calls to the concrete driver.
Also handles brightness scheduling and image enhancement.
"""

import fnmatch
import logging
import os
from datetime import datetime

import pytz

from utils.image_utils import resize_image, change_orientation, apply_image_enhancement
from display.mock_display import MockDisplay

logger = logging.getLogger(__name__)

try:
    from display.lcd_display import LcdDisplay
except ImportError:
    LcdDisplay = None
    logger.info("LCD display not available (missing numpy or /dev/fb0)")

try:
    from display.inky_display import InkyDisplay
except ImportError:
    InkyDisplay = None
    logger.info("Inky display not available (missing inky library)")

try:
    from display.waveshare_display import WaveshareDisplay
except ImportError:
    WaveshareDisplay = None
    logger.info("Waveshare display not available (missing waveshare drivers)")


def _detect_display_type():
    """Auto-detect the connected display hardware.

    Detection order:
    1. Inky e-paper via I2C auto-detection -> inky (most specific)
    2. LCD framebuffer with valid sysfs resolution -> lcd
    3. Fall back to mock for development

    Returns:
        str: The detected display type ("lcd", "inky", or "mock").
    """
    # Try Inky auto-detection first (I2C probe is definitive)
    if InkyDisplay is not None:
        try:
            from inky.auto import auto
            auto()
            logger.info("Auto-detected Inky e-paper display")
            return "inky"
        except Exception:
            logger.debug("Inky auto-detection failed, not an Inky display")

    # Check for LCD framebuffer with valid sysfs resolution.
    # /dev/fb0 exists on all Pis (console framebuffer), so we also verify
    # that the sysfs virtual_size file exists (only present with real HDMI display).
    fb_sysfs = "/sys/class/graphics/fb0/virtual_size"
    if os.path.exists("/dev/fb0") and os.path.exists(fb_sysfs):
        logger.info("Auto-detected LCD display (/dev/fb0 + sysfs present)")
        return "lcd"

    logger.info("No display hardware detected, falling back to mock")
    return "mock"


class DisplayManager:

    """Manages the display and rendering of images."""

    def __init__(self, device_config):

        """
        Initializes the display manager and selects the correct display type
        based on the configuration. If display_type is "auto" or not set,
        attempts auto-detection.

        Args:
            device_config (object): Configuration object containing display settings.

        Raises:
            ValueError: If an unsupported display type is specified.
        """

        self.device_config = device_config
        self._display_blanked = False
        self._brightness_override = None  # Temporary override from dashboard slider
        self._last_period = None  # Track schedule period for auto-clearing override

        display_type = device_config.get_config("display_type", default="auto")

        # Auto-detect if requested or not configured
        if display_type == "auto":
            display_type = _detect_display_type()
            device_config.update_value("display_type", display_type, write=True)
            logger.info(f"Display type auto-detected and saved: {display_type}")

        if display_type == "mock":
            self.display = MockDisplay(device_config)
        elif display_type == "lcd":
            if LcdDisplay is None:
                raise ValueError("LCD display requested but lcd_display module not available")
            self.display = LcdDisplay(device_config)
        elif display_type == "inky":
            if InkyDisplay is None:
                raise ValueError("Inky display requested but inky library not installed")
            self.display = InkyDisplay(device_config)
        elif fnmatch.fnmatch(display_type, "epd*in*"):
            if WaveshareDisplay is None:
                raise ValueError("Waveshare display requested but waveshare drivers not available")
            self.display = WaveshareDisplay(device_config)
        else:
            raise ValueError(f"Unsupported display type: {display_type}")

    def display_image(self, image, image_settings=None):

        """
        Delegates image rendering to the appropriate display instance.

        Args:
            image (PIL.Image): The image to be displayed.
            image_settings (list, optional): List of settings to modify image rendering.

        Raises:
            ValueError: If no valid display instance is found.
        """

        if not hasattr(self, "display"):
            raise ValueError("No valid display instance initialized.")

        # Save the image atomically (temp file + rename) so the web UI
        # never fetches a half-written file
        logger.info(f"Saving image to {self.device_config.current_image_file}")
        tmp_path = self.device_config.current_image_file.replace(".png", "_tmp.png")
        image.save(tmp_path)
        os.replace(tmp_path, self.device_config.current_image_file)

        # Check scheduled brightness — only applies to displays with backlight
        if self.display.has_backlight():
            brightness = self._get_effective_brightness()
            if brightness == 0:
                if not self._display_blanked:
                    self.display.blank_display()
                    self._display_blanked = True
                return

            # Restore display if it was blanked
            if self._display_blanked:
                self.display.unblank_display()
                self._display_blanked = False
        else:
            brightness = 1.0  # E-ink: no backlight, always full brightness for enhancement

        # Convert to RGB once at the start of the pipeline
        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')

        # Resize and adjust orientation
        image = change_orientation(image, self.device_config.get_config("orientation"))
        image = resize_image(image, self.device_config.get_resolution(), image_settings)
        if self.device_config.get_config("inverted_image"): image = image.rotate(180)
        effective_settings = self.device_config.get_config("image_settings") or {}
        effective_settings["brightness"] = brightness
        image = apply_image_enhancement(image, effective_settings)

        # Pass to the concrete instance to render to the device.
        self.display.display_image(image, image_settings)

    def get_current_brightness(self):
        """Return the current brightness value and override state for API use.

        Returns:
            dict: {"brightness": float, "overridden": bool}
        """
        if self.display.has_backlight():
            return {
                "brightness": self._get_effective_brightness(),
                "overridden": self._brightness_override is not None,
            }
        return {"brightness": 1.0, "overridden": False}

    def set_brightness_override(self, value):
        """Set a temporary brightness override from the dashboard slider.

        The override persists until the schedule transitions to the next
        period (day/evening/night), or until manually cleared.

        Args:
            value (float): Brightness value 0.0–2.0 (0 = display off).
        """
        self._brightness_override = value
        logger.info(f"Brightness override set to {value}")

    def clear_brightness_override(self):
        """Clear the temporary brightness override, reverting to schedule."""
        if self._brightness_override is not None:
            self._brightness_override = None
            logger.info("Brightness override cleared")

    def _get_effective_brightness(self):
        """Return brightness with override applied if set."""
        if self._brightness_override is not None:
            return self._brightness_override
        return self._get_scheduled_brightness()

    def get_display_capabilities(self):
        """Return display capability info for the web UI and API."""
        return {
            "display_type": self.display.display_type_name(),
            "has_touch": self.display.has_touch(),
            "has_backlight": self.display.has_backlight(),
            "supports_fast_refresh": self.display.supports_fast_refresh(),
        }

    def _get_current_period(self):
        """Determine the current schedule period name.

        Returns:
            str: "day", "evening", or "night" based on current time and schedule.
                 Returns "day" if schedule is disabled.
        """
        schedule = self.device_config.get_config("brightness_schedule") or {}
        if not schedule.get("enabled"):
            return "day"

        day_start = schedule.get("day_start", "07:00")
        evening_start = schedule.get("evening_start", "18:00")
        night_start = schedule.get("night_start", "22:00")

        tz_str = self.device_config.get_config("timezone", default="UTC")
        current_time = datetime.now(pytz.timezone(tz_str)).strftime("%H:%M")

        times = [day_start, evening_start, night_start]
        if times == sorted(times):
            if current_time >= night_start or current_time < day_start:
                return "night"
            elif current_time >= evening_start:
                return "evening"
            else:
                return "day"
        else:
            if current_time >= day_start and current_time < evening_start:
                return "day"
            elif current_time >= evening_start and current_time < night_start:
                return "evening"
            else:
                return "night"

    def _get_scheduled_brightness(self):
        """Determine the current brightness based on the day/evening/night schedule.

        Returns the appropriate brightness value (float) based on current time
        and the configured schedule. Falls back to day_brightness if schedule
        is disabled or not configured. Also auto-clears any brightness override
        when the schedule period transitions.
        """
        schedule = self.device_config.get_config("brightness_schedule") or {}
        day_brightness = schedule.get("day_brightness", 1.0)

        # Detect period transitions and auto-clear override
        current_period = self._get_current_period()
        if self._last_period is not None and current_period != self._last_period:
            if self._brightness_override is not None:
                logger.info(
                    f"Schedule period changed ({self._last_period} -> {current_period}), "
                    "clearing brightness override"
                )
                self._brightness_override = None
        self._last_period = current_period

        if not schedule.get("enabled"):
            return day_brightness

        evening_brightness = schedule.get("evening_brightness", 0.6)
        night_brightness = schedule.get("night_brightness", 0.3)

        period_map = {"day": day_brightness, "evening": evening_brightness, "night": night_brightness}
        return period_map.get(current_period, day_brightness)
