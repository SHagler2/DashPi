import logging
import os
from utils.app_utils import resolve_path
from utils.image_loader import AdaptiveImageLoader
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGINS_DIR = resolve_path("plugins")

FRAME_STYLES = [
    {
        "name": "None",
        "icon": "frames/blank.png"
    },
    {
        "name": "Corner",
        "icon": "frames/corner.png"
    },
    {
        "name": "Top and Bottom",
        "icon": "frames/top_and_bottom.png"
    },
    {
        "name": "Rectangle",
        "icon": "frames/rectangle.png"
    }
]

class BasePlugin:
    """Base class for all plugins."""
    def __init__(self, config, **dependencies):
        self.config = config

        # Initialize adaptive image loader for device-aware image processing
        self.image_loader = AdaptiveImageLoader()

    def generate_image(self, settings, device_config):
        raise NotImplementedError("generate_image must be implemented by subclasses")

    def cleanup(self, settings):
        """Optional cleanup method that plugins can override to delete associated resources.

        Called when a plugin instance is deleted. Plugins should override this to clean up
        any files, external resources, or other data associated with the plugin instance.

        Args:
            settings: The plugin instance's settings dict, which may contain file paths or other resources
        """
        pass  # Default implementation does nothing

    def get_plugin_id(self):
        return self.config.get("id")

    def get_plugin_dir(self, path=None):
        plugin_dir = os.path.join(PLUGINS_DIR, self.get_plugin_id())
        if path:
            plugin_dir = os.path.join(plugin_dir, path)
        return plugin_dir

    def generate_settings_template(self):
        template_params = {"settings_template": "base_plugin/settings.html"}

        settings_path = self.get_plugin_dir("settings.html")
        if Path(settings_path).is_file():
            template_params["settings_template"] = f"{self.get_plugin_id()}/settings.html"

        template_params['frame_styles'] = FRAME_STYLES
        return template_params

