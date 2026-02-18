from flask import Blueprint, request, jsonify, current_app, render_template, send_from_directory
from plugins.plugin_registry import get_plugin_instance
from utils.app_utils import resolve_path, handle_request_files, parse_form
from refresh_task import ManualRefresh
import json
import os
import logging

logger = logging.getLogger(__name__)
plugin_bp = Blueprint("plugin", __name__)

@plugin_bp.route('/plugin/<plugin_id>')
def plugin_page(plugin_id):
    device_config = current_app.config['DEVICE_CONFIG']
    loop_manager = device_config.get_loop_manager()

    # Check for loop edit mode (coming from loops page)
    loop_name = request.args.get('loop_name', '')
    edit_mode = request.args.get('edit_mode', 'false') == 'true'

    # If editing a loop plugin, get existing settings
    existing_settings = {}
    existing_refresh_interval = None
    if edit_mode and loop_name:
        loop = loop_manager.get_loop(loop_name)
        if loop:
            plugin_ref = next((ref for ref in loop.plugin_order if ref.plugin_id == plugin_id), None)
            if plugin_ref:
                existing_settings = plugin_ref.plugin_settings or {}
                existing_refresh_interval = plugin_ref.refresh_interval_seconds

    # Find the plugin by id
    plugin_config = device_config.get_plugin(plugin_id)
    if plugin_config:
        try:
            plugin = get_plugin_instance(plugin_config)
            template_params = plugin.generate_settings_template()

            # Note: We no longer support editing plugin instances directly from the plugin page
            # Plugin settings are now managed through loops

            template_params["loops"] = loop_manager.get_loop_names()
            template_params["loop_edit_mode"] = edit_mode
            template_params["loop_name"] = loop_name
            template_params["loop_refresh_interval"] = existing_refresh_interval

            # If in edit mode, use existing settings from the loop
            if edit_mode and existing_settings:
                template_params["plugin_settings"] = existing_settings
            elif not edit_mode:
                # Try to inherit settings so users don't re-enter preferences.
                # Priority: 1) last-used settings, 2) existing loop instance
                last_used = device_config.get_config(
                    f"plugin_last_settings_{plugin_id}", default=None
                )
                if last_used:
                    template_params["plugin_settings"] = last_used
                else:
                    for loop in loop_manager.loops:
                        for ref in loop.plugin_order:
                            if ref.plugin_id == plugin_id and ref.plugin_settings:
                                template_params["plugin_settings"] = dict(ref.plugin_settings)
                                break
                        if "plugin_settings" in template_params:
                            break
        except Exception as e:
            logger.exception("EXCEPTION CAUGHT: " + str(e))
            return jsonify({"error": f"An error occurred: {str(e)}"}), 500
        return render_template('plugin.html', plugin=plugin_config, **template_params)
    else:
        return "Plugin not found", 404

@plugin_bp.route('/images/<plugin_id>/<path:filename>')
def image(plugin_id, filename):
    # Resolve plugins directory dynamically
    plugins_dir = resolve_path("plugins")

    # Construct the full path to the plugin's file
    plugin_dir = os.path.join(plugins_dir, plugin_id)

    # Security check to prevent directory traversal
    safe_path = os.path.abspath(os.path.join(plugin_dir, filename))
    if not safe_path.startswith(os.path.abspath(plugins_dir)):
        return "Invalid path", 403

    # Convert to absolute path for send_from_directory
    abs_plugin_dir = os.path.abspath(plugin_dir)

    # Check if the directory and file exist
    if not os.path.isdir(abs_plugin_dir):
        logger.error(f"Plugin directory not found: {abs_plugin_dir}")
        return "Plugin directory not found", 404

    if not os.path.isfile(safe_path):
        logger.error(f"File not found: {safe_path}")
        return "File not found", 404

    # Serve the file from the plugin directory
    return send_from_directory(abs_plugin_dir, filename)

@plugin_bp.route('/update_now_async', methods=['POST'])
def update_now_async():
    """Non-blocking update endpoint. Queues the update and returns immediately.
    Use status polling (e.g. status.json) to track progress."""
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']

    try:
        plugin_settings = parse_form(request.form)
        plugin_settings.update(handle_request_files(request.files))
        plugin_id = plugin_settings.pop("plugin_id")

        # Remember settings for next time the plugin page is opened
        device_config.update_value(
            f"plugin_last_settings_{plugin_id}", dict(plugin_settings)
        )

        if refresh_task.running:
            queued = refresh_task.queue_manual_update(ManualRefresh(plugin_id, plugin_settings))
            if queued:
                return jsonify({"success": True, "message": "Update queued"}), 202
            else:
                return jsonify({"error": "Refresh task not running"}), 500
        else:
            return jsonify({"error": "Refresh task not running"}), 500

    except Exception as e:
        logger.exception(f"Error in update_now_async: {str(e)}")
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@plugin_bp.route('/update_now', methods=['POST'])
def update_now():
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    display_manager = current_app.config['DISPLAY_MANAGER']

    try:
        plugin_settings = parse_form(request.form)
        plugin_settings.update(handle_request_files(request.files))
        plugin_id = plugin_settings.pop("plugin_id")

        # Remember settings for next time the plugin page is opened
        device_config.update_value(
            f"plugin_last_settings_{plugin_id}", dict(plugin_settings)
        )

        # For stocks plugin, merge in saved settings (autoRefresh, etc.) if not in form
        if plugin_id == "stocks":
            saved_settings = device_config.get_config("stocks_plugin_settings", default={})
            for key, value in saved_settings.items():
                if key not in plugin_settings or not plugin_settings.get(key):
                    plugin_settings[key] = value
            logger.debug(f"Stocks update_now with merged settings: {plugin_settings}")

        # Check if refresh task is running
        if refresh_task.running:
            refresh_task.manual_update(ManualRefresh(plugin_id, plugin_settings))
        else:
            # In development mode, directly update the display
            logger.info("Refresh task not running, updating display directly")
            plugin_config = device_config.get_plugin(plugin_id)
            if not plugin_config:
                return jsonify({"error": f"Plugin '{plugin_id}' not found"}), 404

            plugin = get_plugin_instance(plugin_config)
            image = plugin.generate_image(plugin_settings, device_config)
            display_manager.display_image(image, image_settings=plugin_config.get("image_settings", []))

    except Exception as e:
        logger.exception(f"Error in update_now: {str(e)}")
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

    return jsonify({"success": True, "message": "Display updated"}), 200


# Stocks plugin - saved settings and tickers management
@plugin_bp.route('/plugin/stocks/settings', methods=['GET'])
def get_stocks_settings():
    """Get saved stocks plugin settings (autoRefresh, etc.)."""
    device_config = current_app.config['DEVICE_CONFIG']
    settings = device_config.get_config("stocks_plugin_settings", default={})
    return jsonify({"settings": settings})


@plugin_bp.route('/plugin/stocks/settings', methods=['POST'])
def save_stocks_settings():
    """Save stocks plugin settings."""
    device_config = current_app.config['DEVICE_CONFIG']
    data = request.get_json()

    if not data or "settings" not in data:
        return jsonify({"error": "No settings provided"}), 400

    settings = data["settings"]
    device_config.update_value("stocks_plugin_settings", settings, write=True)
    return jsonify({"success": True, "settings": settings})


@plugin_bp.route('/plugin/stocks/tickers', methods=['GET'])
def get_saved_tickers():
    """Get the list of saved stock tickers."""
    device_config = current_app.config['DEVICE_CONFIG']
    saved_tickers = device_config.get_config("stocks_saved_tickers", default=[])
    return jsonify({"tickers": saved_tickers})


@plugin_bp.route('/plugin/stocks/tickers', methods=['POST'])
def save_tickers():
    """Save the list of stock tickers (used for reordering)."""
    device_config = current_app.config['DEVICE_CONFIG']
    data = request.get_json()

    if not data or "tickers" not in data:
        return jsonify({"error": "No tickers provided"}), 400

    new_order = data["tickers"]
    if not isinstance(new_order, list):
        return jsonify({"error": "Tickers must be a list"}), 400

    # Get current tickers to preserve name data
    current_tickers = device_config.get_config("stocks_saved_tickers", default=[])

    # Build lookup of current ticker data
    ticker_data = {}
    for t in current_tickers:
        if isinstance(t, dict):
            ticker_data[t["symbol"]] = t
        else:
            ticker_data[t] = {"symbol": t, "name": t}

    # Reorder based on new_order, preserving ticker data
    reordered = []
    for symbol in new_order[:6]:
        symbol_upper = symbol.strip().upper() if isinstance(symbol, str) else symbol
        if symbol_upper in ticker_data:
            reordered.append(ticker_data[symbol_upper])

    device_config.update_value("stocks_saved_tickers", reordered, write=True)
    return jsonify({"success": True, "tickers": reordered})


@plugin_bp.route('/plugin/stocks/tickers/<ticker>', methods=['DELETE'])
def remove_ticker(ticker):
    """Remove a single ticker from the saved list."""
    device_config = current_app.config['DEVICE_CONFIG']
    saved_tickers = device_config.get_config("stocks_saved_tickers", default=[])

    ticker_upper = ticker.upper()
    # Handle both old format (string) and new format (dict)
    new_list = []
    found = False
    for t in saved_tickers:
        symbol = t["symbol"] if isinstance(t, dict) else t
        if symbol == ticker_upper:
            found = True
        else:
            new_list.append(t)

    if found:
        device_config.update_value("stocks_saved_tickers", new_list, write=True)
        return jsonify({"success": True, "tickers": new_list})

    return jsonify({"error": "Ticker not found"}), 404


@plugin_bp.route('/plugin/stocks/tickers/add', methods=['POST'])
def add_ticker():
    """Add a ticker to the saved list after validating it."""
    device_config = current_app.config['DEVICE_CONFIG']
    data = request.get_json()

    if not data or "ticker" not in data:
        return jsonify({"error": "No ticker provided"}), 400

    ticker = data["ticker"].strip().upper()
    if not ticker:
        return jsonify({"error": "Invalid ticker"}), 400

    saved_tickers = device_config.get_config("stocks_saved_tickers", default=[])

    # Check if ticker already exists (compare symbols only)
    existing_symbols = [t["symbol"] if isinstance(t, dict) else t for t in saved_tickers]
    if ticker in existing_symbols:
        return jsonify({"error": "Ticker already exists", "tickers": saved_tickers}), 400

    if len(saved_tickers) >= 6:
        return jsonify({"error": "Maximum 6 tickers allowed", "tickers": saved_tickers}), 400

    # Validate ticker using yfinance
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        info = stock.info

        # Check if we got valid data
        name = info.get("shortName") or info.get("longName")
        if not name or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
            return jsonify({"error": f"'{ticker}' is not a valid ticker symbol"}), 400

        # Store as object with symbol and name
        ticker_obj = {"symbol": ticker, "name": name}
        saved_tickers.append(ticker_obj)
        device_config.update_value("stocks_saved_tickers", saved_tickers, write=True)
        return jsonify({"success": True, "tickers": saved_tickers, "added": ticker_obj})

    except Exception as e:
        logger.error(f"Error validating ticker {ticker}: {str(e)}")
        return jsonify({"error": f"Could not validate ticker '{ticker}'"}), 400
