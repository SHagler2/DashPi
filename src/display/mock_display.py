"""Mock display driver — saves images to disk for local development."""

import os
import logging
from datetime import datetime
from .abstract_display import AbstractDisplay

logger = logging.getLogger(__name__)

class MockDisplay(AbstractDisplay):
    """Mock display for development without hardware."""
    
    DEFAULT_RESOLUTION = [800, 480]

    def __init__(self, device_config):
        self.device_config = device_config
        resolution = device_config.get_resolution()
        if not resolution or len(resolution) < 2:
            resolution = self.DEFAULT_RESOLUTION
            device_config.update_value("resolution", resolution, write=True)
            logger.info(f"No resolution configured, using default: {resolution}")
        self.width = resolution[0]
        self.height = resolution[1]
        self.output_dir = device_config.get_config('output_dir', 'mock_display_output')
        os.makedirs(self.output_dir, exist_ok=True)
        
    def initialize_display(self):
        """Initialize mock display (no-op for development)."""
        logger.info(f"Mock display initialized: {self.width}x{self.height}")
        
    def blank_display(self):
        """Mock blank (no-op)."""
        logger.info("Mock display blanked")

    def unblank_display(self):
        """Mock unblank (no-op)."""
        logger.info("Mock display unblanked")

    def display_image(self, image, image_settings=None):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self.output_dir, f"display_{timestamp}.png")
        image.save(filepath, "PNG")

        # Also save as latest.png for convenience
        image.save(os.path.join(self.output_dir, 'latest.png'), "PNG")

    # ---- capability flags ---------------------------------------------------

    def has_touch(self):
        return True  # Simulate touch for development

    def has_backlight(self):
        return True

    def supports_fast_refresh(self):
        return True

    def display_type_name(self):
        return "Mock"