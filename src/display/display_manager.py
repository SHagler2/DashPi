import logging
from datetime import datetime

import pytz

from utils.image_utils import resize_image, change_orientation, apply_image_enhancement
from display.mock_display import MockDisplay

logger = logging.getLogger(__name__)

try:
    from display.lcd_display import LcdDisplay
except ImportError:
    logger.info("LCD display not available")

class DisplayManager:

    """Manages the display and rendering of images."""

    def __init__(self, device_config):

        """
        Initializes the display manager and selects the correct display type 
        based on the configuration.

        Args:
            device_config (object): Configuration object containing display settings.

        Raises:
            ValueError: If an unsupported display type is specified.
        """
        
        self.device_config = device_config
        self._display_blanked = False

        display_type = device_config.get_config("display_type", default="lcd")

        if display_type == "mock":
            self.display = MockDisplay(device_config)
        elif display_type == "lcd":
            self.display = LcdDisplay(device_config)
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

        # Save the image (always, so web UI preview stays current)
        logger.info(f"Saving image to {self.device_config.current_image_file}")
        image.save(self.device_config.current_image_file)

        # Check scheduled brightness — 0 means blank the display
        brightness = self._get_scheduled_brightness()
        if brightness == 0:
            if not self._display_blanked:
                self.display.blank_display()
                self._display_blanked = True
            return

        # Restore display if it was blanked
        if self._display_blanked:
            self.display.unblank_display()
            self._display_blanked = False

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
        """Return the current scheduled brightness value for API use."""
        return self._get_scheduled_brightness()

    def _get_scheduled_brightness(self):
        """Determine the current brightness based on the day/evening/night schedule.

        Returns the appropriate brightness value (float) based on current time
        and the configured schedule. Falls back to day_brightness if schedule
        is disabled or not configured.
        """
        schedule = self.device_config.get_config("brightness_schedule") or {}
        day_brightness = schedule.get("day_brightness", 1.0)

        if not schedule.get("enabled"):
            return day_brightness

        evening_brightness = schedule.get("evening_brightness", 0.6)
        night_brightness = schedule.get("night_brightness", 0.3)
        day_start = schedule.get("day_start", "07:00")
        evening_start = schedule.get("evening_start", "18:00")
        night_start = schedule.get("night_start", "22:00")

        # Get current time in device timezone
        tz_str = self.device_config.get_config("timezone", default="UTC")
        current_time = datetime.now(pytz.timezone(tz_str)).strftime("%H:%M")

        # Determine which period the current time falls into.
        # Periods are ordered: day_start -> evening_start -> night_start
        # Night wraps across midnight back to day_start.
        times = [day_start, evening_start, night_start]
        if times == sorted(times):
            # Non-wrapping: all times in chronological order
            if current_time >= night_start or current_time < day_start:
                return night_brightness
            elif current_time >= evening_start:
                return evening_brightness
            else:
                return day_brightness
        else:
            # Wrapping across midnight: night_start is after midnight
            if current_time >= day_start and current_time < evening_start:
                return day_brightness
            elif current_time >= evening_start and current_time < night_start:
                return evening_brightness
            else:
                return night_brightness