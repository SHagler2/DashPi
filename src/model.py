import os
import json
import logging
import random
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class RefreshInfo:
    """Keeps track of refresh metadata.

    Attributes:
        refresh_time (str): ISO-formatted time string of the refresh.
        image_hash (int): SHA-256 hash of the image.
        refresh_type (str): Refresh type ['Manual Update', 'Loop'].
        plugin_id (str): Plugin id of the refresh.
        loop (str): Loop name if refresh_type is 'Loop'.
    """

    def __init__(self, refresh_type, plugin_id, refresh_time, image_hash, loop=None):
        """Initialize RefreshInfo instance."""
        self.refresh_time = refresh_time
        self.image_hash = image_hash
        self.refresh_type = refresh_type
        self.plugin_id = plugin_id
        self.loop = loop

    def get_refresh_datetime(self):
        """Returns the refresh time as a datetime object or None if not set."""
        latest_refresh = None
        if self.refresh_time:
            latest_refresh = datetime.fromisoformat(self.refresh_time)
        return latest_refresh

    def to_dict(self):
        refresh_dict = {
            "refresh_time": self.refresh_time,
            "image_hash": self.image_hash,
            "refresh_type": self.refresh_type,
            "plugin_id": self.plugin_id,
        }
        if self.loop:
            refresh_dict["loop"] = self.loop
        return refresh_dict

    @classmethod
    def from_dict(cls, data):
        return cls(
            refresh_time=data.get("refresh_time"),
            image_hash=data.get("image_hash"),
            refresh_type=data.get("refresh_type"),
            plugin_id=data.get("plugin_id"),
            loop=data.get("loop")
        )

class LoopManager:
    """Manages multiple time-based loops as an alternative to playlists.

    Loop Mode provides a simplified approach where plugins rotate in a sequence
    without individual instance settings. Each plugin uses its default settings.

    Attributes:
        loops (list): List of Loop instances managed by the manager.
        rotation_interval_seconds (int): Global interval between display changes.
        active_loop (str): Name of the currently active loop.
    """
    DEFAULT_ROTATION_INTERVAL = 300  # 5 minutes

    def __init__(self, loops=None, rotation_interval_seconds=None, active_loop=None):
        """Initialize LoopManager with loops and rotation interval."""
        self.loops = loops or []
        self.rotation_interval_seconds = rotation_interval_seconds or self.DEFAULT_ROTATION_INTERVAL
        self.active_loop = active_loop

        # Cache for active loop determination to avoid repeated recalculation
        self._cached_current_time = None
        self._cached_active_loop = None

    def get_loop_names(self):
        """Returns a list of all loop names."""
        return [loop.name for loop in self.loops]

    def get_loop(self, loop_name):
        """Returns the loop with the specified name."""
        return next((loop for loop in self.loops if loop.name == loop_name), None)

    def add_loop(self, name, start_time, end_time):
        """Creates and adds a new loop with the given time range."""
        if self.get_loop(name):
            logger.warning(f"Loop '{name}' already exists.")
            return False
        self.loops.append(Loop(name, start_time, end_time))
        # Invalidate cache since loops changed
        self._cached_active_loop = None
        return True

    def update_loop(self, old_name, new_name, start_time, end_time):
        """Updates an existing loop's name and time range."""
        loop = self.get_loop(old_name)
        if loop:
            loop.name = new_name
            loop.start_time = start_time
            loop.end_time = end_time
            # Invalidate cached time range since times changed
            loop._cached_time_range_minutes = None
            # Invalidate active loop cache since loop properties changed
            self._cached_active_loop = None
            return True
        logger.warning(f"Loop '{old_name}' not found.")
        return False

    def delete_loop(self, name):
        """Deletes the loop with the specified name."""
        self.loops = [loop for loop in self.loops if loop.name != name]
        # Invalidate cache since loops changed
        self._cached_active_loop = None

    def determine_active_loop(self, current_datetime):
        """Determine the active loop based on the current time.

        Uses caching to avoid repeated recalculation when time hasn't changed.
        """
        current_time = current_datetime.strftime("%H:%M")

        # Return cached result if time hasn't changed
        if self._cached_current_time == current_time and self._cached_active_loop is not None:
            return self._cached_active_loop

        # Get active loops that have plugins
        active_loops = [loop for loop in self.loops if loop.is_active(current_time) and loop.plugin_order]
        if not active_loops:
            self._cached_current_time = current_time
            self._cached_active_loop = None
            return None

        # Sort loops by priority (smallest time range first)
        # Note: get_priority() now uses cached time_range_minutes
        active_loops.sort(key=lambda l: l.get_priority())

        # Cache result
        self._cached_current_time = current_time
        self._cached_active_loop = active_loops[0]
        return active_loops[0]

    def to_dict(self):
        return {
            "loops": [loop.to_dict() for loop in self.loops],
            "rotation_interval_seconds": self.rotation_interval_seconds,
            "active_loop": self.active_loop
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            loops=[Loop.from_dict(loop) for loop in data.get("loops", [])],
            rotation_interval_seconds=data.get("rotation_interval_seconds"),
            active_loop=data.get("active_loop")
        )


class Loop:
    """Represents a single time-based loop for plugin rotation.

    A loop defines a time window during which a specific sequence of plugins
    rotates on the display. Unlike playlists, loops don't maintain individual
    plugin settings - each plugin uses its default configuration.

    Attributes:
        name (str): Name of the loop.
        start_time (str): Loop start time in 'HH:MM'.
        end_time (str): Loop end time in 'HH:MM'.
        plugin_order (list): Ordered list of PluginReference objects.
        current_plugin_index (int): Index of the currently displayed plugin.
        randomize (bool): If True, randomly select next plugin instead of sequential order.
    """

    def __init__(self, name, start_time, end_time, plugin_order=None, current_plugin_index=None, randomize=False, next_plugin_index=None):
        self.name = name
        self.start_time = start_time
        self.end_time = end_time
        self.plugin_order = [PluginReference.from_dict(p) for p in (plugin_order or [])]
        self.current_plugin_index = current_plugin_index
        self.randomize = randomize
        self.next_plugin_index = next_plugin_index  # Pre-computed next plugin

        # Cache time range calculation to avoid repeated string parsing
        self._cached_time_range_minutes = None

    def is_active(self, current_time):
        """Check if the loop is active at the given time."""
        if self.start_time <= self.end_time:
            # Non-wrapping window (e.g., 09:00-15:00)
            return self.start_time <= current_time < self.end_time
        else:
            # Wrapping window across midnight (e.g., 21:00-03:00)
            return current_time >= self.start_time or current_time < self.end_time

    def add_plugin(self, plugin_id, refresh_interval_seconds):
        """Add a plugin to this loop's rotation."""
        # Check if plugin already exists in this loop
        if any(ref.plugin_id == plugin_id for ref in self.plugin_order):
            logger.warning(f"Plugin '{plugin_id}' already exists in loop '{self.name}'.")
            return False
        self.plugin_order.append(PluginReference(plugin_id, refresh_interval_seconds))
        return True

    def remove_plugin(self, plugin_id):
        """Remove a plugin from this loop's rotation."""
        initial_count = len(self.plugin_order)
        self.plugin_order = [ref for ref in self.plugin_order if ref.plugin_id != plugin_id]

        if len(self.plugin_order) == initial_count:
            logger.warning(f"Plugin '{plugin_id}' not found in loop '{self.name}'.")
            return False
        return True

    def reorder_plugins(self, plugin_ids):
        """Reorder plugins based on a list of plugin IDs."""
        # Create a mapping of plugin_id to PluginReference
        plugin_map = {ref.plugin_id: ref for ref in self.plugin_order}

        # Reorder based on provided IDs
        new_order = []
        for plugin_id in plugin_ids:
            if plugin_id in plugin_map:
                new_order.append(plugin_map[plugin_id])

        self.plugin_order = new_order

    def get_next_plugin(self):
        """Returns the next plugin reference in rotation.

        If randomize is enabled, selects a random plugin (avoiding the current one if possible).
        Otherwise, cycles through plugins sequentially.

        Also pre-computes the following plugin so it can be displayed in the UI.
        """
        if not self.plugin_order:
            return None

        # Use pre-computed next if available, otherwise compute it
        if self.next_plugin_index is not None:
            self.current_plugin_index = self.next_plugin_index
        elif self.randomize:
            # First time in random mode
            self.current_plugin_index = random.randint(0, len(self.plugin_order) - 1)
        else:
            # First time in sequential mode
            self.current_plugin_index = 0 if self.current_plugin_index is None else (self.current_plugin_index + 1) % len(self.plugin_order)

        # Pre-compute the NEXT plugin for UI display
        self._compute_next_plugin_index()

        return self.plugin_order[self.current_plugin_index]

    def _compute_next_plugin_index(self):
        """Pre-compute the next plugin index for UI display."""
        if not self.plugin_order:
            self.next_plugin_index = None
            return

        if self.randomize:
            # Random selection - avoid current plugin if possible
            if len(self.plugin_order) == 1:
                self.next_plugin_index = 0
            else:
                available_indices = [i for i in range(len(self.plugin_order)) if i != self.current_plugin_index]
                self.next_plugin_index = random.choice(available_indices)
        else:
            # Sequential - next in order
            if self.current_plugin_index is None:
                self.next_plugin_index = 0
            else:
                self.next_plugin_index = (self.current_plugin_index + 1) % len(self.plugin_order)

    def peek_next_plugin(self):
        """Return the pre-computed next plugin without advancing. For UI display."""
        if not self.plugin_order:
            return None
        if self.next_plugin_index is not None:
            return self.plugin_order[self.next_plugin_index]
        # Fallback if not yet computed
        return self.plugin_order[0] if self.plugin_order else None

    def get_priority(self):
        """Determine priority of a loop based on the time range."""
        return self.get_time_range_minutes()

    def get_time_range_minutes(self):
        """Calculate the time difference in minutes between start_time and end_time.

        Results are cached to avoid repeated string parsing on every loop evaluation.
        """
        if self._cached_time_range_minutes is not None:
            return self._cached_time_range_minutes

        start = datetime.strptime(self.start_time, "%H:%M")

        # Handle '24:00' by converting it to '00:00' of the next day
        if self.end_time != "24:00":
            end = datetime.strptime(self.end_time, "%H:%M")
        else:
            end = datetime.strptime("00:00", "%H:%M")
            end += timedelta(days=1)

        # If the window wraps past midnight (e.g., 21:00 -> 03:00), treat end as next day
        if end < start:
            end += timedelta(days=1)

        self._cached_time_range_minutes = int((end - start).total_seconds() // 60)
        return self._cached_time_range_minutes

    def to_dict(self):
        return {
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "plugin_order": [ref.to_dict() for ref in self.plugin_order],
            "current_plugin_index": self.current_plugin_index,
            "randomize": self.randomize,
            "next_plugin_index": self.next_plugin_index
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            name=data["name"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            plugin_order=data.get("plugin_order", []),
            current_plugin_index=data.get("current_plugin_index"),
            randomize=data.get("randomize", False),
            next_plugin_index=data.get("next_plugin_index")
        )


class PluginReference:
    """Reference to a plugin with its refresh timing settings.

    Unlike PluginInstance, this is simplified - settings are optional and
    plugins can use defaults if not provided.

    Attributes:
        plugin_id (str): Plugin identifier.
        refresh_interval_seconds (int): How often to refresh this plugin's data.
        plugin_settings (dict): Optional settings for the plugin. If None/empty, plugin uses defaults.
        latest_refresh_time (str): ISO timestamp of last data refresh.
    """

    def __init__(self, plugin_id, refresh_interval_seconds, plugin_settings=None, latest_refresh_time=None):
        self.plugin_id = plugin_id
        self.refresh_interval_seconds = refresh_interval_seconds
        self.plugin_settings = plugin_settings or {}
        self.latest_refresh_time = latest_refresh_time

    def should_refresh(self, current_time):
        """Check if plugin data needs refresh based on interval."""
        latest_refresh_dt = self.get_latest_refresh_dt()
        if not latest_refresh_dt:
            return True  # Never refreshed, so refresh now

        return (current_time - latest_refresh_dt) >= timedelta(seconds=self.refresh_interval_seconds)

    def get_latest_refresh_dt(self):
        """Returns the latest refresh time as a datetime object, or None if not set."""
        if self.latest_refresh_time:
            return datetime.fromisoformat(self.latest_refresh_time)
        return None

    def to_dict(self):
        return {
            "plugin_id": self.plugin_id,
            "refresh_interval_seconds": self.refresh_interval_seconds,
            "plugin_settings": self.plugin_settings,
            "latest_refresh_time": self.latest_refresh_time
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            plugin_id=data["plugin_id"],
            refresh_interval_seconds=data["refresh_interval_seconds"],
            plugin_settings=data.get("plugin_settings", {}),
            latest_refresh_time=data.get("latest_refresh_time")
        )