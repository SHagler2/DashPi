"""WiFi blueprint — captive portal for WiFi provisioning and reconfiguration.

Serves the WiFi setup web UI when DashPi is in AP hotspot mode. Also handles
captive portal detection (Android/iOS/Windows) to auto-redirect phones to the
setup page. Provides API endpoints for network scanning and credential submission.
"""

import logging

from flask import Blueprint, request, jsonify, current_app, render_template, redirect, url_for
from utils.wifi_manager import STATE_AP_MODE

logger = logging.getLogger(__name__)
wifi_bp = Blueprint("wifi", __name__)


@wifi_bp.route('/wifi')
def wifi_portal():
    """Render the WiFi setup captive portal page.

    Scans for available networks and displays them in a mobile-friendly form.
    Accessible at http://10.42.0.1/wifi when in AP mode.
    """
    wifi_manager = current_app.config.get('WIFI_MANAGER')
    device_config = current_app.config['DEVICE_CONFIG']

    networks = []
    if wifi_manager:
        networks = wifi_manager.scan_networks()

    device_name = device_config.get_config("device_name", default="DashPi")
    return render_template('wifi_setup.html', networks=networks, device_name=device_name)


@wifi_bp.route('/wifi/scan', methods=['GET'])
def wifi_scan():
    """Rescan for available WiFi networks.

    Returns JSON list of networks with ssid, signal strength, and security type.
    Called by the captive portal UI's rescan button.
    """
    wifi_manager = current_app.config.get('WIFI_MANAGER')
    if not wifi_manager:
        return jsonify({"networks": []})

    networks = wifi_manager.scan_networks()
    return jsonify({"networks": networks})


@wifi_bp.route('/wifi/connect', methods=['POST'])
def wifi_connect():
    """Apply WiFi credentials and attempt to connect.

    Stops the AP hotspot, connects to the specified network, and verifies
    internet connectivity. Returns the new IP on success, or an error message
    on failure (AP mode is automatically restarted on failure).
    """
    wifi_manager = current_app.config.get('WIFI_MANAGER')
    display_manager = current_app.config.get('DISPLAY_MANAGER')
    device_config = current_app.config['DEVICE_CONFIG']

    if not wifi_manager:
        return jsonify({"success": False, "error": "WiFi manager not available"}), 500

    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Invalid request"}), 400

    ssid = data.get('ssid', '').strip()
    password = data.get('password', '')

    if not ssid:
        return jsonify({"success": False, "error": "Network name is required"}), 400

    success, result = wifi_manager.connect(ssid, password)

    if success:
        logger.info("WiFi connected successfully: %s (IP: %s)", ssid, result)
        # Update the display with the new connection info
        if display_manager:
            try:
                from utils.app_utils import generate_startup_image
                img = generate_startup_image(device_config.get_resolution())
                display_manager.display_image(img)
            except Exception as e:
                logger.warning("Failed to update display after WiFi connect: %s", e)

        return jsonify({
            "success": True,
            "ip": result,
            "message": f"Connected to {ssid}!"
        })
    else:
        logger.warning("WiFi connection failed: %s — %s", ssid, result)
        # Restart AP mode so user can try again
        device_name = device_config.get_config("device_name", default="DashPi")
        wifi_manager.start_ap_mode(device_name)

        # Restore the setup image on display
        if display_manager:
            try:
                from utils.wifi_display import generate_wifi_setup_image
                ap_ssid = wifi_manager.get_ap_ssid(device_name)
                portal_url = f"http://{wifi_manager.get_hotspot_ip()}/wifi"
                img = generate_wifi_setup_image(
                    device_config.get_resolution(), ap_ssid, portal_url,
                    password=wifi_manager.get_ap_password()
                )
                display_manager.display_image(img)
            except Exception as e:
                logger.warning("Failed to update display after WiFi failure: %s", e)

        return jsonify({
            "success": False,
            "error": "Could not connect. Check the password and try again."
        })


@wifi_bp.route('/wifi/status', methods=['GET'])
def wifi_status():
    """Return current WiFi state for polling during connection attempts."""
    wifi_manager = current_app.config.get('WIFI_MANAGER')
    if not wifi_manager:
        return jsonify({"state": "unknown", "connected": False})

    return jsonify({
        "state": wifi_manager.state,
        "connected": wifi_manager.state != STATE_AP_MODE,
        "ssid": wifi_manager.get_wifi_ssid(),
        "ip": wifi_manager.get_ip_address(),
    })


@wifi_bp.route('/wifi/reconfigure', methods=['POST'])
def wifi_reconfigure():
    """Manually trigger AP mode for WiFi reconfiguration.

    Called from the Settings page when the user wants to change WiFi networks.
    Starts the hotspot and updates the display with setup instructions.
    """
    wifi_manager = current_app.config.get('WIFI_MANAGER')
    display_manager = current_app.config.get('DISPLAY_MANAGER')
    device_config = current_app.config['DEVICE_CONFIG']

    if not wifi_manager:
        return jsonify({"success": False, "error": "WiFi manager not available"}), 500

    device_name = device_config.get_config("device_name", default="DashPi")
    ap_ssid = wifi_manager.get_ap_ssid(device_name)
    success = wifi_manager.start_ap_mode(device_name)

    if success and display_manager:
        try:
            from utils.wifi_display import generate_wifi_setup_image
            portal_url = f"http://{wifi_manager.get_hotspot_ip()}/wifi"
            img = generate_wifi_setup_image(
                device_config.get_resolution(), ap_ssid, portal_url,
                password=wifi_manager.get_ap_password()
            )
            display_manager.display_image(img)
        except Exception as e:
            logger.warning("Failed to display WiFi setup image: %s", e)

    if success:
        return jsonify({
            "success": True,
            "ap_ssid": ap_ssid,
            "message": f"Connect to '{ap_ssid}' to configure WiFi."
        })
    else:
        return jsonify({"success": False, "error": "Failed to start WiFi hotspot"}), 500


@wifi_bp.route('/wifi/switch', methods=['POST'])
def wifi_switch():
    """Switch to a different WiFi network directly (no hotspot needed).

    Called from the Settings page when the device is already on a network
    and the user wants to connect to a different visible network. Unlike
    reconfigure, this doesn't enter AP mode — it just switches networks.
    """
    wifi_manager = current_app.config.get('WIFI_MANAGER')
    display_manager = current_app.config.get('DISPLAY_MANAGER')
    device_config = current_app.config['DEVICE_CONFIG']

    if not wifi_manager:
        return jsonify({"success": False, "error": "WiFi manager not available"}), 500

    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Invalid request"}), 400

    ssid = data.get('ssid', '').strip()
    password = data.get('password', '')

    if not ssid:
        return jsonify({"success": False, "error": "Network name is required"}), 400

    success, result = wifi_manager.connect(ssid, password)

    if success:
        logger.info("WiFi switched successfully: %s (IP: %s)", ssid, result)
        if display_manager:
            try:
                from utils.app_utils import generate_startup_image
                img = generate_startup_image(device_config.get_resolution())
                display_manager.display_image(img)
            except Exception as e:
                logger.warning("Failed to update display after WiFi switch: %s", e)

        return jsonify({
            "success": True,
            "ip": result,
            "ssid": ssid,
            "message": f"Connected to {ssid}!"
        })
    else:
        logger.warning("WiFi switch failed: %s — %s", ssid, result)
        return jsonify({
            "success": False,
            "error": f"Could not connect to '{ssid}'. Check the password and try again."
        })


# --- Captive Portal Detection Endpoints ---
# When a phone connects to the AP hotspot, the OS automatically probes these
# URLs to check for internet access. By redirecting them to /wifi, the phone's
# captive portal browser opens our setup page automatically.

@wifi_bp.route('/generate_204')          # Android
@wifi_bp.route('/gen_204')               # Android alt
def captive_android():
    """Handle Android captive portal detection."""
    wifi_manager = current_app.config.get('WIFI_MANAGER')
    if wifi_manager and wifi_manager.state == STATE_AP_MODE:
        return redirect(url_for('wifi.wifi_portal'))
    # If not in AP mode, return the expected 204 (normal internet behavior)
    return '', 204


@wifi_bp.route('/hotspot-detect.html')          # Apple
@wifi_bp.route('/library/test/success.html')    # Apple alt
def captive_apple():
    """Handle Apple captive portal detection."""
    wifi_manager = current_app.config.get('WIFI_MANAGER')
    if wifi_manager and wifi_manager.state == STATE_AP_MODE:
        return redirect(url_for('wifi.wifi_portal'))
    return '<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>'


@wifi_bp.route('/connecttest.txt')      # Windows
@wifi_bp.route('/ncsi.txt')             # Windows alt
def captive_windows():
    """Handle Windows captive portal detection."""
    wifi_manager = current_app.config.get('WIFI_MANAGER')
    if wifi_manager and wifi_manager.state == STATE_AP_MODE:
        return redirect(url_for('wifi.wifi_portal'))
    return 'Microsoft Connect Test'
