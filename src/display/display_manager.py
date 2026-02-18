import json
import logging

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
        
        # Save the image
        logger.info(f"Saving image to {self.device_config.current_image_file}")
        image.save(self.device_config.current_image_file)

        # Resize and adjust orientation
        image = change_orientation(image, self.device_config.get_config("orientation"))
        image = resize_image(image, self.device_config.get_resolution(), image_settings)
        if self.device_config.get_config("inverted_image"): image = image.rotate(180)
        image = apply_image_enhancement(image, self.device_config.get_config("image_settings"))

        # Pass to the concrete instance to render to the device.
        self.display.display_image(image, image_settings)