import threading
import tempfile
import time
import os
import gc
import json
import logging
import psutil
import pytz
from datetime import datetime, timezone
from plugins.plugin_registry import get_plugin_instance
from utils.image_utils import compute_image_hash
from model import RefreshInfo, LoopManager
from PIL import Image

logger = logging.getLogger(__name__)

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
GLOBAL_STATUS_DIR = os.path.join(SRC_DIR, "static", "images", "plugins")
GLOBAL_STATUS_FILE = os.path.join(GLOBAL_STATUS_DIR, "refresh_status.json")
PLUGINS_DIR = os.path.join(SRC_DIR, "plugins")

class RefreshTask:
    """Handles the logic for refreshing the display using a background thread."""

    def __init__(self, device_config, display_manager):
        self.device_config = device_config
        self.display_manager = display_manager

        self.thread = None
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.running = False
        self.manual_update_request = ()

        self.refresh_event = threading.Event()
        self.refresh_event.set()
        self.refresh_result = {}

        # Config write batching - only write periodically to reduce SD card wear
        self.refresh_counter = 0
        self.config_write_interval = 12  # Write every 12 refreshes (~1 hour at 5-min intervals)

        # Auto-refresh tracking - load from config if available (survives restarts)
        saved_auto_refresh = device_config.get_config("auto_refresh_tracking", default={})
        self.auto_refresh_plugin_settings = saved_auto_refresh.get("plugin_settings")
        last_time_str = saved_auto_refresh.get("last_display_time")
        if last_time_str:
            try:
                self.last_display_time = datetime.fromisoformat(last_time_str)
            except (ValueError, TypeError):
                self.last_display_time = None
        else:
            self.last_display_time = None

        # Track when the last LOOP rotation happened (distinct from auto-refresh).
        # This prevents auto-refreshing plugins from blocking loop rotation.
        last_rotation_str = saved_auto_refresh.get("last_loop_rotation_time")
        if last_rotation_str:
            try:
                self.last_loop_rotation_time = datetime.fromisoformat(last_rotation_str)
            except (ValueError, TypeError):
                self.last_loop_rotation_time = None
        else:
            self.last_loop_rotation_time = None

        # First run after boot uses a short delay so the display updates quickly
        self.first_run = True

        # If no auto-refresh tracking but current plugin is stocks, restore from saved settings
        if not self.auto_refresh_plugin_settings:
            refresh_info = device_config.get_refresh_info()
            if refresh_info and refresh_info.plugin_id == "stocks":
                stocks_settings = device_config.get_config("stocks_plugin_settings", default={})
                if stocks_settings.get("autoRefresh"):
                    self.auto_refresh_plugin_settings = stocks_settings
                    # Use current time as last display time so we refresh after the interval
                    self.last_display_time = datetime.now(pytz.timezone(device_config.get_config("timezone", default="UTC")))
                    logger.info(f"Restored auto-refresh tracking for stocks: {stocks_settings.get('autoRefresh')} min")

    def start(self):
        """Starts the background thread for refreshing the display."""
        if not self.thread or not self.thread.is_alive():
            logger.info("Starting refresh task")
            os.makedirs(GLOBAL_STATUS_DIR, exist_ok=True)
            # Clean up any orphaned temp files from previous crashes
            for f in os.listdir(GLOBAL_STATUS_DIR):
                if f.endswith('.tmp'):
                    try: os.remove(os.path.join(GLOBAL_STATUS_DIR, f))
                    except OSError: pass
            self._set_global_status("idle", "Starting up...")
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.running = True
            self.thread.start()

    def stop(self):
        """Stops the refresh task by notifying the background thread to exit."""
        with self.condition:
            self.running = False
            self.condition.notify_all()  # Wake the thread to let it exit
        if self.thread:
            logger.info("Stopping refresh task")
            self.thread.join()
        # Write config on shutdown to persist final state
        logger.info("Writing final config on shutdown")
        self.device_config.write_config()

    def _run(self):
        """Background task that manages the periodic refresh of the display.

        This function runs in a loop, sleeping for a configured duration (`plugin_cycle_interval_seconds`) or until
        manually triggered via `manual_update()`. Determines the next plugin to refresh based on active playlists and
        updates the display accordingly.

        Workflow:
        1. Waits for the configured sleep duration or until notified of a manual update.
        2. Checks if a manual update has been requested:
        - If so, refreshes the specified plugin immediately.
        3. Otherwise, determines the next plugin to refresh based on the active playlist and generates an image.
        4. Compares the image hash with the last displayed image hash.
        - If the image has changed, updates the display.
        - If the image is the same, skips the refresh.
        5. Updates the refresh metadata in the device configuration.
        6. Repeats the process until `stop()` is called.

        Handles any exceptions that occur during the refresh process and ensures the refresh event is set 
        to indicate completion.

        Exceptions:
        - Captures and logs any unexpected errors during execution to prevent the thread from exiting.
        """
        while True:
            try:
                with self.condition:
                    # Get sleep time from loop manager
                    loop_manager = self.device_config.get_loop_manager()
                    sleep_time = loop_manager.rotation_interval_seconds

                    # Check if current plugin has auto-refresh - use shorter interval if so
                    auto_refresh_seconds = self._get_auto_refresh_seconds()
                    use_auto_refresh = False
                    if auto_refresh_seconds:
                        if auto_refresh_seconds < sleep_time:
                            sleep_time = auto_refresh_seconds
                            use_auto_refresh = True
                            logger.info(f"Auto-refresh configured: {auto_refresh_seconds}s, using as sleep interval")
                        else:
                            use_auto_refresh = True  # Still want auto-refresh, but loop interval is shorter
                            logger.info(f"Auto-refresh configured: {auto_refresh_seconds}s, but loop interval {sleep_time}s is shorter")

                    # On first run after boot, use a short delay so the display updates quickly
                    if self.first_run:
                        sleep_time = 10
                        logger.info("First run after boot, using 10s startup delay")

                    # Update status with next change countdown
                    if sleep_time >= 3600:
                        remaining = f"{sleep_time / 3600:.1f}h"
                    elif sleep_time >= 60:
                        remaining = f"{int(sleep_time / 60)}m"
                    else:
                        remaining = f"{int(sleep_time)}s"
                    self._set_global_status("idle", f"Next change in {remaining}")

                    # Wait for sleep_time or until notified
                    self.condition.wait(timeout=sleep_time)
                    self.first_run = False
                    self.refresh_result = {}
                    self.refresh_event.clear()

                    # Exit if `stop()` is called
                    if not self.running:
                        break

                    latest_refresh = self.device_config.get_refresh_info()
                    current_dt = self._get_current_datetime()

                    refresh_action = None
                    if self.manual_update_request:
                        # handle immediate update request
                        logger.info("Manual update requested")
                        refresh_action = self.manual_update_request
                        self.manual_update_request = ()
                        pname = self._get_display_name(refresh_action.get_plugin_id())
                        self._set_global_status("refreshing", f"Updating {pname}...", pname, refresh_action.get_plugin_id())
                    else:

                        if self.device_config.get_config("log_system_stats"):
                            self.log_system_stats()

                        # Check if loop rotation is enabled
                        loop_enabled = self.device_config.get_config("loop_enabled", default=True)

                        # Check if loop rotation is overdue (takes priority over auto-refresh)
                        loop_rotation_due = False
                        if loop_enabled:
                            loop_manager = self.device_config.get_loop_manager()
                            rotation_interval = loop_manager.rotation_interval_seconds
                            if self.last_loop_rotation_time:
                                elapsed_since_rotation = (current_dt - self.last_loop_rotation_time).total_seconds()
                                loop_rotation_due = elapsed_since_rotation >= rotation_interval
                            else:
                                # No rotation tracked yet - first run, do rotation
                                loop_rotation_due = True

                        if loop_rotation_due:
                            # Loop rotation takes priority over auto-refresh
                            logger.info(f"Running interval refresh check. | current_time: {current_dt.strftime('%Y-%m-%d %H:%M:%S')}")

                            loop_manager = self.device_config.get_loop_manager()
                            loop, plugin_ref = self._determine_next_plugin_loop_mode(loop_manager, current_dt)
                            if plugin_ref:
                                refresh_action = LoopRefresh(loop, plugin_ref)
                                pname = self._get_display_name(plugin_ref.plugin_id)
                                self._set_global_status("refreshing", f"Loading {pname}...", pname, plugin_ref.plugin_id)
                        elif use_auto_refresh and self._should_auto_refresh(current_dt):
                            # Auto-refresh current plugin (only if loop rotation isn't due)
                            logger.info(f"Auto-refreshing current plugin: {latest_refresh.plugin_id}")
                            refresh_action = AutoRefresh(latest_refresh.plugin_id, self.auto_refresh_plugin_settings)
                            pname = self._get_display_name(latest_refresh.plugin_id)
                            self._set_global_status("refreshing", f"Auto-refreshing {pname}...", pname, latest_refresh.plugin_id)
                        elif not loop_enabled:
                            # Loop is disabled - but if auto-refresh is configured, we should
                            # still wait for the next auto-refresh interval, not skip entirely
                            if use_auto_refresh:
                                elapsed = (current_dt - self.last_display_time).total_seconds() if self.last_display_time else 0
                                logger.info(f"Loop disabled, auto-refresh waiting (elapsed: {elapsed:.0f}s / {self._get_auto_refresh_seconds()}s)")
                            else:
                                logger.info("Loop rotation is disabled, no action needed")
                            continue

                    if refresh_action:
                        plugin_config = self.device_config.get_plugin(refresh_action.get_plugin_id())
                        if plugin_config is None:
                            logger.error(f"Plugin config not found for '{refresh_action.get_plugin_id()}'.")
                            self._set_global_status("error", f"Plugin not found: {refresh_action.get_plugin_id()}")
                            continue
                        plugin = get_plugin_instance(plugin_config)
                        plugin_name = plugin_config.get("display_name", refresh_action.get_plugin_id())
                        plugin_id = refresh_action.get_plugin_id()

                        self._set_global_status("generating", f"Generating {plugin_name}...", plugin_name, plugin_id)
                        image = refresh_action.execute(plugin, self.device_config, current_dt)

                        # Add plugin icon overlay if enabled
                        if self.device_config.get_config("show_plugin_icon", default=False):
                            image = self._add_plugin_icon_overlay(image, plugin_id)

                        self._set_global_status("processing", f"Processing {plugin_name}...", plugin_name, plugin_id)
                        image_hash = compute_image_hash(image)

                        refresh_info = refresh_action.get_refresh_info()
                        refresh_info.update({"refresh_time": current_dt.isoformat(), "image_hash": image_hash})
                        # check if image is the same as current image
                        if image_hash != latest_refresh.image_hash:
                            self._set_global_status("displaying", f"Sending to display: {plugin_name}...", plugin_name, plugin_id)
                            logger.info(f"Updating display. | refresh_info: {refresh_info}")
                            self.display_manager.display_image(image, image_settings=plugin.config.get("image_settings", []))
                            # Simple log for easy scanning of display history
                            logger.info(f"DISPLAYED: {plugin_name}")
                            self._set_global_status("displayed", f"Displayed: {plugin_name}", plugin_name, plugin_id)
                        else:
                            logger.info(f"Image already displayed, skipping refresh. | refresh_info: {refresh_info}")
                            self._set_global_status("idle", f"No change: {plugin_name}", plugin_name, plugin_id)

                        # update latest refresh data in the device config (in-memory only)
                        self.device_config.refresh_info = RefreshInfo(**refresh_info)

                        # Track loop rotation time (distinct from auto-refresh time)
                        # This ensures the countdown timer reflects actual loop rotations,
                        # not auto-refresh cycles that shouldn't reset it.
                        if isinstance(refresh_action, LoopRefresh):
                            self.last_loop_rotation_time = current_dt

                        # Track plugin settings for auto-refresh
                        plugin_settings = getattr(refresh_action, 'plugin_settings', None)
                        if plugin_settings is None and hasattr(refresh_action, 'plugin_reference'):
                            plugin_settings = dict(refresh_action.plugin_reference.plugin_settings or {})
                            # If loop plugin has a refresh interval but no explicit autoRefresh,
                            # use the loop's per-plugin refresh interval as auto-refresh.
                            # This ensures plugins like ShazamPi that need continuous refresh
                            # keep refreshing while displayed.
                            if not plugin_settings.get('autoRefresh'):
                                ref = refresh_action.plugin_reference
                                if ref.refresh_interval_seconds and ref.refresh_interval_seconds < loop_manager.rotation_interval_seconds:
                                    interval_minutes = ref.refresh_interval_seconds / 60
                                    if interval_minutes > 0:
                                        plugin_settings['autoRefresh'] = str(interval_minutes)
                                        logger.info(f"Deriving autoRefresh={interval_minutes}min from loop refresh_interval for {ref.plugin_id}")
                        self._update_auto_refresh_tracking(plugin_settings, current_dt)

                        # Batch config writes to reduce SD card wear
                        # Only write periodically, not on every refresh
                        self.refresh_counter += 1
                        if self.refresh_counter >= self.config_write_interval:
                            logger.debug(f"Writing config to disk (batched after {self.refresh_counter} refreshes)")
                            self.device_config.write_config()
                            self.refresh_counter = 0

                        # Clean up memory after successful refresh to prevent accumulation
                        gc.collect()

            except Exception as e:
                logger.exception('Exception during refresh')
                self.refresh_result["exception"] = e  # Capture exception
                self._set_global_status("error", f"Error: {e}")
                # Trigger garbage collection to clean up any partially-loaded resources
                # from failed image generation (PIL Images, HTTP connections, etc.)
                gc.collect()
            finally:
                self.refresh_event.set()

    def manual_update(self, refresh_action):
        """Manually triggers an update for the specified plugin id and plugin settings by notifying the background process.

        This method BLOCKS until the refresh completes. For non-blocking behavior, use queue_manual_update().
        """
        if self.running:
            with self.condition:
                self.manual_update_request = refresh_action
                self.refresh_result = {}
                self.refresh_event.clear()

                self.condition.notify_all()  # Wake the thread to process manual update

            self.refresh_event.wait()
            if self.refresh_result.get("exception"):
                raise self.refresh_result.get("exception")
        else:
            logger.warning("Background refresh task is not running, unable to do a manual update")

    def queue_manual_update(self, refresh_action):
        """Queues a manual update without blocking. Returns immediately.

        Use this for async operations where you don't need to wait for the refresh to complete.
        The refresh will happen in the background thread.

        Returns:
            bool: True if the update was queued successfully, False if the task is not running.
        """
        if self.running:
            with self.condition:
                self.manual_update_request = refresh_action
                self.refresh_result = {}
                self.refresh_event.clear()
                self.condition.notify_all()  # Wake the thread to process manual update
            return True
        else:
            logger.warning("Background refresh task is not running, unable to queue manual update")
            return False

    def signal_config_change(self, write_immediately=False):
        """Notify the background thread that config has changed (e.g., interval updated).

        Args:
            write_immediately: If True, force an immediate config write (for user-initiated changes)
        """
        if write_immediately:
            logger.debug("Config change detected, writing immediately")
            self.device_config.write_config()
            self.refresh_counter = 0  # Reset counter after immediate write
        if self.running:
            with self.condition:
                self.condition.notify_all()

    def _get_current_datetime(self):
        """Retrieves the current datetime based on the device's configured timezone."""
        tz_str = self.device_config.get_config("timezone", default="UTC")
        return datetime.now(pytz.timezone(tz_str))

    def _get_display_name(self, plugin_id):
        """Get the human-readable display name for a plugin ID."""
        cfg = self.device_config.get_plugin(plugin_id)
        return cfg.get("display_name", plugin_id) if cfg else plugin_id

    def _set_global_status(self, stage, detail="", plugin_name="", plugin_id=""):
        """Write current refresh status to a JSON file for the loops page to poll.

        Uses atomic write (write to temp file + rename) to prevent torn reads.
        """
        try:
            # Check if this plugin has its own status.json for granular stages
            has_plugin_status = False
            if plugin_id:
                plugin_status_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins", plugin_id, "status.json")
                has_plugin_status = os.path.exists(plugin_status_path)

            status = {
                "stage": stage,
                "detail": detail,
                "plugin_name": plugin_name,
                "plugin_id": plugin_id,
                "has_plugin_status": has_plugin_status,
                "timestamp": time.time(),
            }
            fd, tmp_path = tempfile.mkstemp(dir=GLOBAL_STATUS_DIR, suffix='.tmp')
            os.fchmod(fd, 0o644)
            with os.fdopen(fd, 'w') as f:
                json.dump(status, f)
            os.rename(tmp_path, GLOBAL_STATUS_FILE)
        except Exception:
            pass  # Non-critical

    def _add_plugin_icon_overlay(self, image, plugin_id):
        """Add a small plugin icon with dark backing circle in the bottom-right corner."""
        try:
            from PIL import ImageDraw

            icon_path = os.path.join(PLUGINS_DIR, plugin_id, "icon.png")
            if not os.path.exists(icon_path):
                return image

            icon = Image.open(icon_path).convert("RGBA")

            # Scale icon to ~5% of image height
            img_w, img_h = image.size
            icon_size = max(28, int(img_h * 0.05))
            icon = icon.resize((icon_size, icon_size), Image.LANCZOS)

            # Create backing circle (slightly larger than icon)
            circle_padding = max(4, int(icon_size * 0.2))
            circle_size = icon_size + circle_padding * 2
            circle = Image.new("RGBA", (circle_size, circle_size), (0, 0, 0, 0))
            circle_draw = ImageDraw.Draw(circle)
            circle_draw.ellipse([0, 0, circle_size - 1, circle_size - 1], fill=(0, 0, 0, 140))

            # Convert icon to white silhouette (preserve alpha, make RGB white)
            r, g, b, a = icon.split()
            white = Image.new("L", icon.size, 255)
            icon = Image.merge("RGBA", (white, white, white, a))

            # Paste icon centered on circle
            circle.paste(icon, (circle_padding, circle_padding), icon)

            # Position in bottom-right with padding
            padding = max(6, int(img_h * 0.01))
            x = img_w - circle_size - padding
            y = img_h - circle_size - padding

            # Paste onto image
            img = image.copy()
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            img.paste(circle, (x, y), circle)

            return img
        except Exception as e:
            logger.debug(f"Could not add plugin icon overlay: {e}")
            return image

    def _get_auto_refresh_seconds(self):
        """Check if the currently displayed plugin has auto-refresh configured.

        Returns the auto-refresh interval in seconds, or None if not configured.
        """
        if not self.auto_refresh_plugin_settings:
            return None

        auto_refresh = self.auto_refresh_plugin_settings.get("autoRefresh")
        if auto_refresh:
            try:
                minutes = float(auto_refresh)
                if minutes > 0:
                    return int(minutes * 60)
            except (ValueError, TypeError):
                pass
        return None

    def _should_auto_refresh(self, current_dt):
        """Check if we should auto-refresh the current plugin based on elapsed time."""
        if not self.last_display_time or not self.auto_refresh_plugin_settings:
            return False

        auto_refresh_seconds = self._get_auto_refresh_seconds()
        if not auto_refresh_seconds:
            return False

        elapsed = (current_dt - self.last_display_time).total_seconds()
        return elapsed >= auto_refresh_seconds

    def _update_auto_refresh_tracking(self, plugin_settings, current_dt):
        """Update tracking for auto-refresh after displaying a plugin."""
        self.auto_refresh_plugin_settings = plugin_settings or {}
        self.last_display_time = current_dt
        # Persist to config so it survives service restarts
        tracking = {
            "plugin_settings": self.auto_refresh_plugin_settings,
            "last_display_time": current_dt.isoformat() if current_dt else None,
            "last_loop_rotation_time": self.last_loop_rotation_time.isoformat() if self.last_loop_rotation_time else None,
        }
        self.device_config.update_value("auto_refresh_tracking", tracking, write=False)  # Don't write immediately, will be batched

    def _determine_next_plugin_loop_mode(self, loop_manager, current_dt):
        """Determines the next plugin to refresh in loop mode based on the active loop and rotation."""
        loop = loop_manager.determine_active_loop(current_dt)
        if not loop:
            loop_manager.active_loop = None
            logger.info("No active loop determined.")
            return None, None

        loop_manager.active_loop = loop.name
        if not loop.plugin_order:
            logger.info(f"Active loop '{loop.name}' has no plugins.")
            return None, None

        # In loop mode, rotation happens automatically after sleep interval
        # Just get the next plugin in sequence
        plugin_ref = loop.get_next_plugin()
        logger.info(f"Determined next plugin. | active_loop: {loop.name} | plugin_id: {plugin_ref.plugin_id}")

        return loop, plugin_ref

    def log_system_stats(self):
        # Use non-blocking CPU sampling (returns estimate since last call)
        # This avoids 1+ second blocking delay on every refresh
        metrics = {
            'cpu_percent': psutil.cpu_percent(interval=None),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_percent': psutil.disk_usage('/').percent,
            'load_avg_1_5_15': os.getloadavg(),
            'swap_percent': psutil.swap_memory().percent,
            'net_io': dict(
                bytes_sent=net_io.bytes_sent,
                bytes_recv=net_io.bytes_recv
            ) if (net_io := psutil.net_io_counters()) else {}
        }

        logger.info(f"System Stats: {metrics}")

class RefreshAction:
    """Base class for a refresh action. Subclasses should override the methods below."""
    
    def refresh(self, plugin, device_config, current_dt):
        """Perform a refresh operation and return the updated image."""
        raise NotImplementedError("Subclasses must implement the refresh method.")
    
    def get_refresh_info(self):
        """Return refresh metadata as a dictionary."""
        raise NotImplementedError("Subclasses must implement the get_refresh_info method.")
    
    def get_plugin_id(self):
        """Return the plugin ID associated with this refresh."""
        raise NotImplementedError("Subclasses must implement the get_plugin_id method.")

class AutoRefresh(RefreshAction):
    """Performs an auto-refresh of the currently displayed plugin.

    Used when a plugin has auto-refresh configured to update its data
    at a more frequent interval than the loop rotation.

    Attributes:
        plugin_id (str): The ID of the plugin to refresh.
        plugin_settings (dict): The settings for the plugin.
    """

    def __init__(self, plugin_id: str, plugin_settings: dict):
        self.plugin_id = plugin_id
        self.plugin_settings = plugin_settings or {}

    def execute(self, plugin, device_config, current_dt: datetime):
        """Performs an auto-refresh using the stored plugin settings."""
        return plugin.generate_image(self.plugin_settings, device_config)

    def get_refresh_info(self):
        """Return refresh metadata as a dictionary."""
        return {"refresh_type": "Auto Refresh", "plugin_id": self.plugin_id}

    def get_plugin_id(self):
        """Return the plugin ID associated with this refresh."""
        return self.plugin_id


class ManualRefresh(RefreshAction):
    """Performs a manual refresh based on a plugin's ID and its associated settings.
    
    Attributes:
        plugin_id (str): The ID of the plugin to refresh.
        plugin_settings (dict): The settings for the manual refresh.
    """

    def __init__(self, plugin_id: str, plugin_settings: dict):
        self.plugin_id = plugin_id
        self.plugin_settings = plugin_settings

    def execute(self, plugin, device_config, current_dt: datetime):
        """Performs a manual refresh using the stored plugin ID and settings."""
        return plugin.generate_image(self.plugin_settings, device_config)

    def get_refresh_info(self):
        """Return refresh metadata as a dictionary."""
        return {"refresh_type": "Manual Update", "plugin_id": self.plugin_id}

    def get_plugin_id(self):
        """Return the plugin ID associated with this refresh."""
        return self.plugin_id

class LoopRefresh(RefreshAction):
    """Performs refresh using a plugin reference within a loop context.

    Unlike PlaylistRefresh, this doesn't use custom settings - plugins use their default configuration.
    Data refresh is controlled by the plugin_reference's refresh_interval_seconds.

    Attributes:
        loop: The Loop object
        plugin_reference: PluginReference to display
        force: If True, bypass cache and always regenerate image
    """

    def __init__(self, loop, plugin_reference, force=False):
        self.loop = loop
        self.plugin_reference = plugin_reference
        self.force = force

    def get_refresh_info(self):
        """Return refresh metadata as a dictionary."""
        return {
            "refresh_type": "Loop",
            "loop": self.loop.name,
            "plugin_id": self.plugin_reference.plugin_id
        }

    def get_plugin_id(self):
        """Return the plugin ID associated with this refresh."""
        return self.plugin_reference.plugin_id

    def execute(self, plugin, device_config, current_dt: datetime):
        """Performs a refresh for the plugin reference in loop mode.

        Checks if the plugin's data needs refreshing based on its refresh interval.
        If data refresh is needed, generates a new image. Otherwise, uses cached image.

        Special handling: If randomization is enabled (randomizeWpotd, randomizeApod),
        always generate a new image to get a different random selection.
        """
        # Determine the file path for the plugin's image
        plugin_image_path = os.path.join(device_config.plugin_image_dir, f"loop_{self.plugin_reference.plugin_id}.png")

        # Check if this plugin has randomization enabled
        settings = self.plugin_reference.plugin_settings or {}
        is_randomized = (
            settings.get("randomizeWpotd") == "true" or
            settings.get("randomizeApod") == "true"
        )

        # Check if data refresh is needed (or forced, or randomized)
        if self.force or is_randomized or self.plugin_reference.should_refresh(current_dt):
            reason = "forced" if self.force else ("randomized" if is_randomized else "interval elapsed")
            logger.info(f"Refreshing plugin data ({reason}). | plugin_id: '{self.plugin_reference.plugin_id}'")
            # Generate a new image with plugin's settings (or empty dict if none)
            image = plugin.generate_image(self.plugin_reference.plugin_settings, device_config)
            image.save(plugin_image_path)
            self.plugin_reference.latest_refresh_time = current_dt.isoformat()
        else:
            logger.info(f"Plugin data still fresh, using cached image. | plugin_id: {self.plugin_reference.plugin_id}")
            # Load the existing image from disk if it exists
            if os.path.exists(plugin_image_path):
                with Image.open(plugin_image_path) as img:
                    image = img.copy()
            else:
                # First time displaying this plugin, generate new image
                logger.info(f"No cached image found, generating new image. | plugin_id: {self.plugin_reference.plugin_id}")
                image = plugin.generate_image(self.plugin_reference.plugin_settings, device_config)
                image.save(plugin_image_path)
                self.plugin_reference.latest_refresh_time = current_dt.isoformat()

        return image