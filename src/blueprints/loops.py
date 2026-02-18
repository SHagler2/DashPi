from flask import Blueprint, request, jsonify, current_app, render_template
from utils.time_utils import calculate_seconds
from utils.http_client import get_http_session
import logging
import requests  # Still needed for exception handling

logger = logging.getLogger(__name__)
loops_bp = Blueprint("loops", __name__)

@loops_bp.route('/loops')
def loops_page():
    """Main loops configuration page"""
    device_config = current_app.config['DEVICE_CONFIG']
    loop_manager = device_config.get_loop_manager()
    refresh_info = device_config.get_refresh_info()
    plugins_list = device_config.get_plugins()

    return render_template(
        'loops.html',
        loop_config=loop_manager.to_dict(),
        refresh_info=refresh_info.to_dict(),
        plugins={p["id"]: p for p in plugins_list},
        all_plugins=plugins_list
    )

@loops_bp.route('/create_loop', methods=['POST'])
def create_loop():
    """Create a new loop"""
    device_config = current_app.config['DEVICE_CONFIG']
    loop_manager = device_config.get_loop_manager()

    data = request.json
    name = data.get("name")
    start_time = data.get("start_time")
    end_time = data.get("end_time")

    # Validation
    if not name or not start_time or not end_time:
        return jsonify({"error": "Missing required fields"}), 400

    if not loop_manager.add_loop(name, start_time, end_time):
        return jsonify({"error": f"Loop '{name}' already exists"}), 400

    device_config.write_config()

    return jsonify({"success": True, "message": f"Created loop '{name}'"})

@loops_bp.route('/update_loop', methods=['POST'])
def update_loop():
    """Update loop properties"""
    device_config = current_app.config['DEVICE_CONFIG']
    loop_manager = device_config.get_loop_manager()

    data = request.json
    old_name = data.get("old_name")
    new_name = data.get("new_name")
    start_time = data.get("start_time")
    end_time = data.get("end_time")

    if not loop_manager.update_loop(old_name, new_name, start_time, end_time):
        return jsonify({"error": f"Loop '{old_name}' not found"}), 404

    device_config.write_config()

    return jsonify({"success": True, "message": f"Updated loop '{new_name}'"})

@loops_bp.route('/delete_loop', methods=['POST'])
def delete_loop():
    """Delete a loop"""
    device_config = current_app.config['DEVICE_CONFIG']
    loop_manager = device_config.get_loop_manager()

    data = request.json
    loop_name = data.get("loop_name")

    if not loop_manager.get_loop(loop_name):
        return jsonify({"error": f"Loop '{loop_name}' not found"}), 404

    loop_manager.delete_loop(loop_name)
    device_config.write_config()

    return jsonify({"success": True, "message": f"Deleted loop '{loop_name}'"})

@loops_bp.route('/add_plugin_to_loop', methods=['POST'])
def add_plugin_to_loop():
    """Add plugin to a loop's rotation"""
    device_config = current_app.config['DEVICE_CONFIG']
    loop_manager = device_config.get_loop_manager()

    data = request.json
    loop_name = data.get("loop_name")
    plugin_id = data.get("plugin_id")
    refresh_interval = data.get("refresh_interval_seconds")
    plugin_settings = data.get("plugin_settings", {})

    loop = loop_manager.get_loop(loop_name)
    if not loop:
        return jsonify({"error": "Loop not found"}), 404

    # Validate plugin exists
    plugin_config = device_config.get_plugin(plugin_id)
    if not plugin_config:
        return jsonify({"error": "Plugin not found"}), 404

    if not loop.add_plugin(plugin_id, refresh_interval):
        return jsonify({"error": f"Plugin '{plugin_id}' already in loop"}), 400

    # Add settings if provided
    for plugin_ref in loop.plugin_order:
        if plugin_ref.plugin_id == plugin_id:
            plugin_ref.plugin_settings = plugin_settings
            break

    device_config.write_config()

    return jsonify({"success": True, "message": f"Added plugin to '{loop_name}'"})

@loops_bp.route('/remove_plugin_from_loop', methods=['POST'])
def remove_plugin_from_loop():
    """Remove plugin from loop rotation"""
    device_config = current_app.config['DEVICE_CONFIG']
    loop_manager = device_config.get_loop_manager()

    data = request.json
    loop_name = data.get("loop_name")
    plugin_id = data.get("plugin_id")

    loop = loop_manager.get_loop(loop_name)
    if not loop:
        return jsonify({"error": "Loop not found"}), 404

    if not loop.remove_plugin(plugin_id):
        return jsonify({"error": "Plugin not found in loop"}), 404

    device_config.write_config()

    return jsonify({"success": True, "message": f"Removed plugin from '{loop_name}'"})

@loops_bp.route('/reorder_plugins', methods=['POST'])
def reorder_plugins():
    """Reorder plugins in a loop"""
    device_config = current_app.config['DEVICE_CONFIG']
    loop_manager = device_config.get_loop_manager()

    data = request.json
    loop_name = data.get("loop_name")
    plugin_ids = data.get("plugin_ids")

    loop = loop_manager.get_loop(loop_name)
    if not loop:
        return jsonify({"error": "Loop not found"}), 404

    loop.reorder_plugins(plugin_ids)
    device_config.write_config()

    return jsonify({"success": True, "message": "Plugin order updated"})

@loops_bp.route('/update_rotation_interval', methods=['POST'])
def update_rotation_interval():
    """Update global rotation interval"""
    device_config = current_app.config['DEVICE_CONFIG']
    loop_manager = device_config.get_loop_manager()

    data = request.json
    interval = data.get("interval")
    unit = data.get("unit")

    if not interval or not unit:
        return jsonify({"error": "Missing interval or unit"}), 400

    interval_seconds = calculate_seconds(int(interval), unit)
    loop_manager.rotation_interval_seconds = interval_seconds

    device_config.write_config()

    # Signal refresh task to update timing
    refresh_task = current_app.config['REFRESH_TASK']
    refresh_task.signal_config_change()

    return jsonify({"success": True, "message": f"Rotation interval set to {interval} {unit}"})

@loops_bp.route('/update_plugin_settings', methods=['POST'])
def update_plugin_settings():
    """Update plugin settings within a loop"""
    device_config = current_app.config['DEVICE_CONFIG']
    loop_manager = device_config.get_loop_manager()

    data = request.json
    loop_name = data.get("loop_name")
    plugin_id = data.get("plugin_id")
    plugin_settings = data.get("plugin_settings", {})
    refresh_interval = data.get("refresh_interval_seconds")

    loop = loop_manager.get_loop(loop_name)
    if not loop:
        return jsonify({"error": "Loop not found"}), 404

    # Find and update plugin reference
    plugin_ref = next((ref for ref in loop.plugin_order if ref.plugin_id == plugin_id), None)
    if not plugin_ref:
        return jsonify({"error": "Plugin not found in loop"}), 404

    plugin_ref.plugin_settings = plugin_settings
    if refresh_interval:
        plugin_ref.refresh_interval_seconds = refresh_interval

    device_config.write_config()

    return jsonify({"success": True, "message": "Plugin settings updated"})

@loops_bp.route('/toggle_loop_randomize', methods=['POST'])
def toggle_loop_randomize():
    """Toggle randomize setting for a loop"""
    device_config = current_app.config['DEVICE_CONFIG']
    loop_manager = device_config.get_loop_manager()

    data = request.json
    loop_name = data.get("loop_name")

    loop = loop_manager.get_loop(loop_name)
    if not loop:
        return jsonify({"error": "Loop not found"}), 404

    # Toggle the randomize setting
    loop.randomize = not loop.randomize
    device_config.write_config()

    status = "Random" if loop.randomize else "Sequential"
    return jsonify({
        "success": True,
        "randomize": loop.randomize,
        "message": f"Loop '{loop_name}' now uses {status} order"
    })

@loops_bp.route('/search_city', methods=['POST'])
def search_city():
    """Search for cities using geocoding API"""
    data = request.json
    city_name = data.get("city_name", "").strip()

    if not city_name:
        return jsonify({"error": "City name is required"}), 400

    try:
        # Use Open-Meteo geocoding API (free, no key needed)
        session = get_http_session()
        response = session.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city_name, "count": 5, "language": "en", "format": "json"},
            timeout=10
        )

        if response.status_code != 200:
            return jsonify({"error": "Geocoding service unavailable"}), 503

        result = response.json()
        results = result.get("results", [])

        if not results:
            return jsonify({"error": f"No cities found matching '{city_name}'"}), 404

        # Format results for display
        cities = []
        for city in results:
            city_info = {
                "name": city.get("name"),
                "country": city.get("country"),
                "admin1": city.get("admin1", ""),  # State/Province
                "latitude": city.get("latitude"),
                "longitude": city.get("longitude"),
                "display": f"{city.get('name')}, {city.get('admin1', city.get('country'))}"
            }
            cities.append(city_info)

        return jsonify({"success": True, "cities": cities})

    except requests.exceptions.RequestException as e:
        logger.error(f"Geocoding API error: {e}")
        return jsonify({"error": "Failed to search for city"}), 500

@loops_bp.route('/refresh_plugin_now', methods=['POST'])
def refresh_plugin_now():
    """Immediately queue a refresh for a specific plugin from a loop.

    Returns 202 Accepted immediately - the refresh happens asynchronously in the background.
    """
    from refresh_task import LoopRefresh

    device_config = current_app.config['DEVICE_CONFIG']
    loop_manager = device_config.get_loop_manager()
    refresh_task = current_app.config['REFRESH_TASK']

    data = request.json
    loop_name = data.get("loop_name")
    plugin_id = data.get("plugin_id")

    loop = loop_manager.get_loop(loop_name)
    if not loop:
        return jsonify({"error": "Loop not found"}), 404

    # Find the plugin reference in the loop
    plugin_ref = next((ref for ref in loop.plugin_order if ref.plugin_id == plugin_id), None)
    if not plugin_ref:
        return jsonify({"error": "Plugin not found in loop"}), 404

    try:
        # Queue the refresh without blocking - returns immediately
        refresh_action = LoopRefresh(loop, plugin_ref, force=True)
        queued = refresh_task.queue_manual_update(refresh_action)

        if queued:
            # Return 202 Accepted - refresh is happening in background
            return jsonify({
                "success": True,
                "message": f"Refreshing {plugin_id}... Display will update shortly."
            }), 202
        else:
            return jsonify({"error": "Refresh task not running"}), 503
    except Exception as e:
        logger.error(f"Error queuing refresh: {e}")
        return jsonify({"error": str(e)}), 500
