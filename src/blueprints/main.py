"""Main blueprint — dashboard, display, diagnostics, and loop control API."""

from flask import Blueprint, request, jsonify, current_app, render_template, send_file
import logging
import os
import time
from datetime import datetime

logger = logging.getLogger(__name__)

main_bp = Blueprint("main", __name__)

def get_version():
    """Read version from VERSION file."""
    version_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'VERSION')
    try:
        with open(version_file, 'r') as f:
            return f.read().strip()
    except Exception:
        return "2.0.0"

@main_bp.route('/')
def main_page():
    """Dashboard home page — plugin grid with loop status."""
    device_config = current_app.config['DEVICE_CONFIG']
    display_manager = current_app.config.get('DISPLAY_MANAGER')
    loop_enabled = device_config.get_config("loop_enabled", default=True)
    loop_override = device_config.get_loop_override()
    has_backlight = display_manager.display.has_backlight() if display_manager else False
    return render_template('dash.html',
                         config=device_config.get_config(),
                         plugins=device_config.get_plugins(),
                         loop_enabled=loop_enabled,
                         loop_override=loop_override,
                         has_backlight=has_backlight)

@main_bp.route('/display')
def display_page():
    """Fullscreen display view - shows just the current image with auto-refresh."""
    return render_template('display.html')

@main_bp.route('/diagnostics')
def diagnostics_page():
    """System diagnostics page with live Pi metrics."""
    return render_template('diagnostics.html')

@main_bp.route('/api/current_image')
def get_current_image():
    """Serve current_image.png with conditional request support (If-Modified-Since)."""
    image_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'images', 'current_image.png')
    
    if not os.path.exists(image_path):
        return jsonify({"error": "Image not found"}), 404
    
    # Get the file's last modified time (truncate to seconds to match HTTP header precision)
    file_mtime = int(os.path.getmtime(image_path))
    last_modified = datetime.fromtimestamp(file_mtime)
    
    # Check If-Modified-Since header
    if_modified_since = request.headers.get('If-Modified-Since')
    if if_modified_since:
        try:
            # Parse the If-Modified-Since header
            client_mtime = datetime.strptime(if_modified_since, '%a, %d %b %Y %H:%M:%S %Z')
            client_mtime_seconds = int(client_mtime.timestamp())
            
            # Compare (both now in seconds, no sub-second precision)
            if file_mtime <= client_mtime_seconds:
                return '', 304
        except (ValueError, AttributeError) as e:
            logger.debug("Could not parse If-Modified-Since header: %s", e)
    
    # Send the file with Last-Modified header
    response = send_file(image_path, mimetype='image/png')
    response.headers['Last-Modified'] = last_modified.strftime('%a, %d %b %Y %H:%M:%S GMT')
    response.headers['Cache-Control'] = 'no-cache'
    return response


@main_bp.route('/api/plugin_order', methods=['POST'])
def save_plugin_order():
    """Save the custom plugin order."""
    device_config = current_app.config['DEVICE_CONFIG']

    data = request.get_json() or {}
    order = data.get('order', [])

    if not isinstance(order, list):
        return jsonify({"error": "Order must be a list"}), 400

    device_config.set_plugin_order(order)

    return jsonify({"success": True})

@main_bp.route('/toggle_loop', methods=['POST'])
def toggle_loop():
    """Enable or disable loop rotation."""
    device_config = current_app.config['DEVICE_CONFIG']

    data = request.get_json() or {}
    enabled = data.get('enabled')

    if enabled is None:
        return jsonify({"error": "enabled field is required"}), 400

    try:
        device_config.update_value("loop_enabled", enabled, write=True)

        # Signal refresh task to pause/resume based on new setting
        refresh_task = current_app.config['REFRESH_TASK']
        refresh_task.signal_config_change()

        return jsonify({
            "success": True,
            "message": f"Loop rotation {'enabled' if enabled else 'disabled'}",
            "enabled": enabled
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@main_bp.route('/api/skip_to_next', methods=['POST'])
def skip_to_next():
    """Skip to the next plugin in the loop immediately."""
    from refresh_task import LoopRefresh

    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config.get('REFRESH_TASK')

    if not refresh_task or not refresh_task.running:
        return jsonify({"error": "Refresh task not running"}), 503

    try:
        loop_manager = device_config.get_loop_manager()
        current_dt = datetime.now()

        # Determine active loop and get next plugin
        loop = loop_manager.determine_active_loop(current_dt)
        if not loop or not loop.plugin_order:
            return jsonify({"error": "No active loop or no plugins in loop"}), 400

        # Get the next plugin (loop.get_next_plugin() advances the index)
        plugin_ref = loop.get_next_plugin()

        # Queue the refresh (non-blocking)
        refresh_action = LoopRefresh(loop, plugin_ref, force=True)
        refresh_task.queue_manual_update(refresh_action)

        # Get display name for response
        plugin_config = device_config.get_plugin(plugin_ref.plugin_id)
        plugin_name = plugin_config.get("display_name", plugin_ref.plugin_id) if plugin_config else plugin_ref.plugin_id

        return jsonify({
            "success": True,
            "message": f"Skipping to {plugin_name}",
            "plugin_id": plugin_ref.plugin_id,
            "plugin_name": plugin_name
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@main_bp.route('/api/pin_plugin', methods=['POST'])
def pin_plugin():
    """Pin a plugin to override the loop schedule."""
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config.get('REFRESH_TASK')

    data = request.get_json() or {}
    plugin_id = data.get('plugin_id')

    if not plugin_id:
        return jsonify({"error": "plugin_id is required"}), 400

    plugin_config = device_config.get_plugin(plugin_id)
    if not plugin_config:
        return jsonify({"error": f"Plugin '{plugin_id}' not found"}), 404

    try:
        device_config.set_loop_override({"type": "plugin", "plugin_id": plugin_id})
        if refresh_task:
            refresh_task.signal_config_change()
        plugin_name = plugin_config.get("display_name", plugin_id)
        return jsonify({"success": True, "message": f"Pinned: {plugin_name}", "plugin_id": plugin_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@main_bp.route('/api/override_loop', methods=['POST'])
def override_loop():
    """Activate a loop override, bypassing the time-based schedule."""
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config.get('REFRESH_TASK')

    data = request.get_json() or {}
    loop_name = data.get('loop_name')

    if not loop_name:
        return jsonify({"error": "loop_name is required"}), 400

    loop_manager = device_config.get_loop_manager()
    loop = loop_manager.get_loop(loop_name)
    if not loop:
        return jsonify({"error": f"Loop '{loop_name}' not found"}), 404

    try:
        device_config.set_loop_override({"type": "loop", "loop_name": loop_name})
        if refresh_task:
            refresh_task.signal_config_change()
        return jsonify({"success": True, "message": f"Override active: {loop_name}", "loop_name": loop_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@main_bp.route('/api/clear_override', methods=['POST'])
def clear_override():
    """Clear any active override and resume normal schedule."""
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config.get('REFRESH_TASK')

    try:
        device_config.clear_loop_override()
        if refresh_task:
            refresh_task.signal_config_change()
        return jsonify({"success": True, "message": "Override cleared, resuming schedule"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@main_bp.route('/api/next_change_time')
def get_next_change_time():
    """Get time remaining until next loop/display change."""
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config.get('REFRESH_TASK')

    if not refresh_task or not refresh_task.running:
        return jsonify({"error": "Refresh task not running"}), 503

    # Get the rotation interval from loop manager
    loop_manager = device_config.get_loop_manager()
    interval_seconds = loop_manager.rotation_interval_seconds

    # Get the last refresh time and current plugin from device config
    refresh_info = device_config.get_refresh_info()
    last_refresh = refresh_info.get_refresh_datetime()

    # Get current plugin display name
    current_plugin_id = refresh_info.plugin_id
    current_plugin_name = "Unknown"
    if current_plugin_id:
        plugin_config = device_config.get_plugin(current_plugin_id)
        if plugin_config:
            current_plugin_name = plugin_config.get("display_name", current_plugin_id)

    # Determine next plugin from loop (uses pre-computed next for random mode)
    next_plugin_name = "Unknown"
    loop = loop_manager.determine_active_loop(datetime.now(refresh_info.get_refresh_datetime().tzinfo) if last_refresh else datetime.now())
    if loop and loop.plugin_order:
        next_ref = loop.peek_next_plugin()
        if next_ref:
            next_plugin_config = device_config.get_plugin(next_ref.plugin_id)
            if next_plugin_config:
                next_plugin_name = next_plugin_config.get("display_name", next_ref.plugin_id)

    # Use last loop rotation time (not last refresh time) for countdown
    # so that auto-refreshing plugins don't reset the countdown
    refresh_task = current_app.config.get('REFRESH_TASK')
    last_rotation = getattr(refresh_task, 'last_loop_rotation_time', None) if refresh_task else None

    if last_rotation:
        now = datetime.now(last_rotation.tzinfo)
        elapsed = (now - last_rotation).total_seconds()
        remaining = max(0, interval_seconds - elapsed)
    elif last_refresh:
        # Fallback to last refresh time if no rotation tracked yet
        now = datetime.now(last_refresh.tzinfo)
        elapsed = (now - last_refresh).total_seconds()
        remaining = max(0, interval_seconds - elapsed)
    else:
        # No refresh yet, assume full interval remaining
        remaining = interval_seconds

    # Check if loop is enabled
    loop_enabled = device_config.get_config("loop_enabled", default=True)

    # Get current brightness level and override state
    display_manager = current_app.config.get('DISPLAY_MANAGER')
    brightness_info = display_manager.get_current_brightness() if display_manager else {"brightness": 1.0, "overridden": False}
    current_brightness = brightness_info["brightness"]
    brightness_overridden = brightness_info["overridden"]

    # Get override info with display name
    loop_override = device_config.get_loop_override()
    if loop_override and loop_override.get("type") == "plugin":
        override_plugin = device_config.get_plugin(loop_override.get("plugin_id"))
        if override_plugin:
            loop_override = dict(loop_override)  # Don't mutate config
            loop_override["display_name"] = override_plugin.get("display_name", loop_override.get("plugin_id"))

    return jsonify({
        "success": True,
        "loop_enabled": loop_enabled,
        "interval_seconds": interval_seconds,
        "remaining_seconds": int(remaining),
        "next_change_in": format_time(int(remaining)),
        "current_plugin": current_plugin_name,
        "current_plugin_id": current_plugin_id,
        "next_plugin": next_plugin_name,
        "current_brightness": current_brightness,
        "brightness_overridden": brightness_overridden,
        "override": loop_override
    })

@main_bp.route('/api/set_brightness', methods=['POST'])
def set_brightness():
    """Set a temporary brightness override from the dashboard slider.

    The override persists until the schedule transitions to the next
    period (day/evening/night), or until manually cleared.
    """
    from refresh_task import ManualRefresh

    display_manager = current_app.config.get('DISPLAY_MANAGER')
    if not display_manager:
        return jsonify({"error": "Display manager not initialized"}), 503

    if not display_manager.display.has_backlight():
        return jsonify({"error": "Display does not support brightness control"}), 400

    data = request.get_json() or {}
    brightness = data.get('brightness')

    if brightness is None:
        return jsonify({"error": "brightness is required"}), 400

    try:
        brightness = float(brightness)
    except (TypeError, ValueError):
        return jsonify({"error": "brightness must be a number"}), 400

    if not (0 <= brightness <= 2.0):
        return jsonify({"error": "brightness must be between 0 and 2.0"}), 400

    display_manager.set_brightness_override(brightness)

    # Re-render current image with new brightness
    refresh_task = current_app.config.get('REFRESH_TASK')
    if refresh_task and refresh_task.running:
        refresh_task.queue_manual_update(ManualRefresh(force=True))

    return jsonify({
        "success": True,
        "message": f"Brightness set to {int(brightness * 100)}%",
        "brightness": brightness
    })


@main_bp.route('/api/clear_brightness_override', methods=['POST'])
def clear_brightness_override():
    """Clear the temporary brightness override, reverting to schedule."""
    from refresh_task import ManualRefresh

    display_manager = current_app.config.get('DISPLAY_MANAGER')
    if not display_manager:
        return jsonify({"error": "Display manager not initialized"}), 503

    display_manager.clear_brightness_override()

    # Re-render current image with scheduled brightness
    refresh_task = current_app.config.get('REFRESH_TASK')
    if refresh_task and refresh_task.running:
        refresh_task.queue_manual_update(ManualRefresh(force=True))

    return jsonify({"success": True, "message": "Brightness override cleared"})


@main_bp.route('/api/display_capabilities')
def get_display_capabilities():
    """Return display capability info for the web UI."""
    display_manager = current_app.config.get('DISPLAY_MANAGER')
    if not display_manager:
        return jsonify({"error": "Display manager not initialized"}), 503
    return jsonify(display_manager.get_display_capabilities())

@main_bp.route('/api/weather_location')
def get_weather_location():
    """Return the weather plugin's saved location for use as a default by other plugins."""
    device_config = current_app.config['DEVICE_CONFIG']
    try:
        loop_manager = device_config.get_loop_manager()
        for loop in loop_manager.loops:
            for ref in loop.plugin_order:
                if ref.plugin_id == "weather" and ref.plugin_settings:
                    lat = ref.plugin_settings.get("latitude")
                    lon = ref.plugin_settings.get("longitude")
                    if lat is not None and lon is not None:
                        return jsonify({"latitude": lat, "longitude": lon})
    except Exception as e:
        logger.debug("Could not retrieve weather location from loops: %s", e)
    return jsonify({"latitude": None, "longitude": None})

@main_bp.route('/api/diagnostics')
def get_diagnostics():
    """Return Pi system metrics for diagnostics panel."""
    import psutil
    metrics = {}
    try:
        # CPU
        metrics["cpu_percent"] = psutil.cpu_percent(interval=0.5)
        metrics["load_avg"] = list(os.getloadavg())

        # Memory
        mem = psutil.virtual_memory()
        metrics["mem_total_mb"] = round(mem.total / 1024 / 1024)
        metrics["mem_used_mb"] = round(mem.used / 1024 / 1024)
        metrics["mem_percent"] = mem.percent
        swap = psutil.swap_memory()
        metrics["swap_used_mb"] = round(swap.used / 1024 / 1024)
        metrics["swap_total_mb"] = round(swap.total / 1024 / 1024)

        # Disk
        disk = psutil.disk_usage('/')
        metrics["disk_total_gb"] = round(disk.total / 1024 / 1024 / 1024, 1)
        metrics["disk_used_gb"] = round(disk.used / 1024 / 1024 / 1024, 1)
        metrics["disk_percent"] = disk.percent

        # Temperature
        try:
            temps = psutil.sensors_temperatures()
            if 'cpu_thermal' in temps:
                metrics["temp_c"] = temps['cpu_thermal'][0].current
            else:
                # Fallback: read directly
                with open('/sys/class/thermal/thermal_zone0/temp') as f:
                    metrics["temp_c"] = round(int(f.read().strip()) / 1000, 1)
        except Exception:
            metrics["temp_c"] = None

        # Uptime
        metrics["uptime_seconds"] = int(time.time() - psutil.boot_time())

        # Throttle status (Pi-specific)
        try:
            import subprocess
            result = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True, timeout=2)
            throttled = result.stdout.strip().split('=')[-1]
            metrics["throttled"] = throttled
            # Decode common flags
            val = int(throttled, 16)
            metrics["undervoltage_now"] = bool(val & 0x1)
            metrics["throttled_now"] = bool(val & 0x4)
            metrics["temp_limit_now"] = bool(val & 0x8)
        except Exception:
            metrics["throttled"] = None

        # WiFi signal strength
        try:
            with open('/proc/net/wireless') as f:
                for line in f:
                    if 'wlan0' in line:
                        parts = line.split()
                        metrics["wifi_link_quality"] = int(float(parts[2]))
                        metrics["wifi_signal_dbm"] = int(float(parts[3]))
                        break
        except Exception:
            metrics["wifi_link_quality"] = None
            metrics["wifi_signal_dbm"] = None

        # Network I/O
        try:
            net = psutil.net_io_counters()
            metrics["net_bytes_sent"] = net.bytes_sent
            metrics["net_bytes_recv"] = net.bytes_recv
        except Exception:
            pass

        # App process stats
        try:
            proc = psutil.Process()
            metrics["app_cpu"] = proc.cpu_percent(interval=0)
            metrics["app_mem_mb"] = round(proc.memory_info().rss / 1024 / 1024)
        except Exception:
            pass

    except ImportError:
        return jsonify({"error": "psutil not installed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(metrics)


def format_time(seconds):
    """Format seconds into human-readable time."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}m {secs}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"