import os
import json
import logging
import tempfile
from dotenv import load_dotenv
from model import RefreshInfo, LoopManager

logger = logging.getLogger(__name__)

class Config:
    # Base path for the project directory
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    # File paths relative to the script's directory
    config_file = os.path.join(BASE_DIR, "config", "device.json")

    # File path for storing the current image being displayed
    current_image_file = os.path.join(BASE_DIR, "static", "images", "current_image.png")

    # Directory path for storing plugin instance images
    plugin_image_dir = os.path.join(BASE_DIR, "static", "images", "plugins")

    def __init__(self):
        self.config = self.read_config()
        self.plugins_list = self.read_plugins_list()
        self.loop_manager = self.load_loop_manager()
        self.refresh_info = self.load_refresh_info()
        # Load .env once at startup
        load_dotenv(override=True)

    def read_config(self):
        """Reads the device config JSON file and returns it as a dictionary."""
        logger.debug(f"Reading device config from {self.config_file}")
        with open(self.config_file) as f:
            config = json.load(f)

        logger.debug("Loaded config:\n%s", json.dumps(config, indent=3))

        return config

    def read_plugins_list(self):
        """Reads the plugin-info.json config JSON from each plugin folder. Excludes the base plugin."""
        # Iterate over all plugin folders
        plugins_list = []
        for plugin in sorted(os.listdir(os.path.join(self.BASE_DIR, "plugins"))):
            plugin_path = os.path.join(self.BASE_DIR, "plugins", plugin)
            if os.path.isdir(plugin_path) and plugin != "__pycache__":
                # Check if the plugin-info.json file exists
                plugin_info_file = os.path.join(plugin_path, "plugin-info.json")
                if os.path.isfile(plugin_info_file):
                    logger.debug(f"Reading plugin info from {plugin_info_file}")
                    with open(plugin_info_file) as f:
                        plugin_info = json.load(f)
                    plugins_list.append(plugin_info)

        return plugins_list

    def write_config(self):
        """Updates the cached config from the model objects and writes to the config file atomically.

        Uses atomic write pattern (write to temp file, then rename) to prevent
        config corruption if power is lost during write.
        """
        logger.debug(f"Writing device config to {self.config_file}")
        self.update_value("loop_config", self.loop_manager.to_dict())
        self.update_value("refresh_info", self.refresh_info.to_dict())

        # Atomic write: write to temp file first, then rename
        config_dir = os.path.dirname(self.config_file)
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=config_dir,
                                              suffix='.tmp', delete=False) as tmp_file:
                json.dump(self.config, tmp_file, indent=4)
                tmp_path = tmp_file.name
            # Atomic rename (on POSIX systems)
            os.replace(tmp_path, self.config_file)
        except Exception as e:
            logger.error(f"Failed to write config atomically: {e}")
            # Fallback to direct write if atomic fails
            with open(self.config_file, 'w') as outfile:
                json.dump(self.config, outfile, indent=4)

    def get_config(self, key=None, default=None):
        """Gets the value of a specific configuration key or returns the entire config if none provided."""
        if key is not None:
            return self.config.get(key, default if default is not None else {})
        return self.config

    def get_plugins(self):
        """Returns the list of plugin configurations, sorted by custom order if set."""
        plugin_order = self.config.get('plugin_order', [])

        if not plugin_order:
            return self.plugins_list

        # Create a dict for quick lookup
        plugins_dict = {p['id']: p for p in self.plugins_list}

        # Build ordered list
        ordered = []
        for plugin_id in plugin_order:
            if plugin_id in plugins_dict:
                ordered.append(plugins_dict.pop(plugin_id))

        # Append any remaining plugins not in the order (new plugins)
        ordered.extend(plugins_dict.values())

        return ordered

    def set_plugin_order(self, order):
        """Sets the custom plugin display order."""
        self.update_value('plugin_order', order, write=True)

    def get_plugin(self, plugin_id):
        """Finds and returns a plugin config by its ID."""
        return next((plugin for plugin in self.plugins_list if plugin['id'] == plugin_id), None)

    def get_resolution(self):
        """Returns the display resolution as a tuple (width, height) from the configuration."""
        resolution = self.get_config("resolution")
        width, height = resolution
        return (int(width), int(height))

    def update_config(self, config):
        """Updates the config with the new values provided and writes to the config file."""
        self.config.update(config)
        self.write_config()

    def update_value(self, key, value, write=False):
        """Updates a specific key in the configuration with a new value and optionally writes it to the config file."""
        self.config[key] = value
        if write:
            self.write_config()

    def load_env_key(self, key):
        """Returns the value of an environment variable."""
        return os.getenv(key)

    def reload_env(self):
        """Reloads the .env file. Call after modifying API keys."""
        load_dotenv(override=True)

    def load_refresh_info(self):
        """Loads the refresh information from the config."""
        return RefreshInfo.from_dict(self.get_config("refresh_info"))

    def get_refresh_info(self):
        """Returns the refresh information."""
        return self.refresh_info

    def load_loop_manager(self):
        """Loads the loop manager object from the config."""
        loop_config = self.get_config("loop_config", default={})
        return LoopManager.from_dict(loop_config)

    def get_loop_manager(self):
        """Returns the loop manager."""
        return self.loop_manager
