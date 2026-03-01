"""WiFi manager — handles AP hotspot mode, network scanning, and credential management.

Uses NetworkManager (nmcli) to manage WiFi state. When no WiFi is available,
starts a hotspot so users can connect with their phone and configure credentials
via the captive portal web UI.
"""

import logging
import os
import socket
import subprocess
import time
import threading

logger = logging.getLogger(__name__)

# AP mode settings
AP_SSID_SUFFIX = "-Setup"
AP_IP = "192.168.4.1"
AP_CONNECTION_NAME = "DashPi-Hotspot"

# States
STATE_CONNECTED = "CONNECTED"
STATE_AP_MODE = "AP_MODE"
STATE_CONNECTING = "CONNECTING"
STATE_DISCONNECTED = "DISCONNECTED"


def _is_pi():
    """Check if running on a Raspberry Pi (vs Mac dev machine)."""
    return os.path.exists("/proc/device-tree/model")


def _run_nmcli(args, timeout=15):
    """Run an nmcli command and return (success, stdout).

    Args:
        args: List of nmcli arguments (without 'nmcli' prefix).
        timeout: Command timeout in seconds.

    Returns:
        Tuple of (success: bool, output: str).
    """
    cmd = ["nmcli"] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        else:
            logger.warning("nmcli failed: %s → %s", " ".join(cmd), result.stderr.strip())
            return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        logger.error("nmcli timed out: %s", " ".join(cmd))
        return False, "timeout"
    except FileNotFoundError:
        logger.error("nmcli not found — NetworkManager not installed")
        return False, "nmcli not found"


class WifiManager:
    """Manages WiFi connectivity, AP hotspot mode, and network provisioning.

    On a Raspberry Pi with NetworkManager, this class uses nmcli to:
    - Detect WiFi connectivity
    - Scan for available networks
    - Start/stop an AP hotspot for captive portal provisioning
    - Connect to a new WiFi network with provided credentials

    In dev mode (non-Pi), all operations are no-ops that return mock data.
    """

    def __init__(self):
        self.state = STATE_DISCONNECTED
        self._lock = threading.Lock()
        self._previous_connection = None  # Name of the WiFi connection before AP mode
        self._is_pi = _is_pi()

        # Check initial state
        if self._is_pi and self.check_connectivity():
            self.state = STATE_CONNECTED

    def check_connectivity(self):
        """Check if the device has internet connectivity.

        Uses a TCP connection to Google's public DNS (8.8.8.8:53) with a
        short timeout. Returns True if reachable, False otherwise.
        """
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            return True
        except OSError:
            return False

    def get_wifi_ssid(self):
        """Get the currently connected WiFi SSID, or None."""
        if not self._is_pi:
            return "DevNetwork"
        try:
            output = subprocess.check_output(
                ["iwgetid", "-r"], text=True, timeout=5
            ).strip()
            return output or None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return None

    def get_ip_address(self):
        """Get the device's LAN IP address, or None if unavailable."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except OSError:
            return None

    def scan_networks(self):
        """Scan for available WiFi networks.

        Returns a list of dicts with keys: ssid, signal, security.
        Sorted by signal strength (strongest first). Duplicates removed.
        """
        if not self._is_pi:
            # Dev mode mock data
            return [
                {"ssid": "HomeNetwork", "signal": 85, "security": "WPA2"},
                {"ssid": "Neighbor-5G", "signal": 60, "security": "WPA3"},
                {"ssid": "CoffeeShop", "signal": 45, "security": "WPA2"},
                {"ssid": "OpenNetwork", "signal": 30, "security": ""},
            ]

        # Force a fresh scan first
        _run_nmcli(["dev", "wifi", "rescan"], timeout=10)
        time.sleep(2)  # Give scan time to complete

        success, output = _run_nmcli(
            ["-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"]
        )
        if not success:
            logger.error("WiFi scan failed: %s", output)
            return []

        networks = []
        seen_ssids = set()
        for line in output.splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue
            ssid = parts[0].strip()
            if not ssid or ssid in seen_ssids:
                continue
            seen_ssids.add(ssid)
            try:
                signal = int(parts[1])
            except ValueError:
                signal = 0
            security = parts[2].strip()
            networks.append({
                "ssid": ssid,
                "signal": signal,
                "security": security,
            })

        networks.sort(key=lambda n: n["signal"], reverse=True)
        return networks

    def get_ap_ssid(self, device_name="DashPi"):
        """Generate the AP hotspot SSID from the device name.

        Example: device_name="Lumi" → "Lumi-Setup"
        """
        return f"{device_name}{AP_SSID_SUFFIX}"

    def start_ap_mode(self, device_name="DashPi"):
        """Start a WiFi hotspot for captive portal provisioning.

        Creates an open (no password) WiFi hotspot that users can connect to
        with their phone. The captive portal web UI runs on the existing
        Flask server at http://192.168.4.1.

        Args:
            device_name: Device name used to generate the AP SSID.

        Returns:
            True if hotspot started successfully, False otherwise.
        """
        if not self._is_pi:
            logger.info("[DEV] Would start AP mode: %s", self.get_ap_ssid(device_name))
            self.state = STATE_AP_MODE
            return True

        with self._lock:
            ap_ssid = self.get_ap_ssid(device_name)
            logger.info("Starting WiFi hotspot: %s", ap_ssid)

            # Remember current connection for later restoration
            self._previous_connection = self._get_active_wifi_connection()

            # Remove any existing hotspot connection profile to start clean
            _run_nmcli(["connection", "delete", AP_CONNECTION_NAME], timeout=10)

            # Create and activate hotspot
            # nmcli dev wifi hotspot creates an AP with DHCP built in
            success, output = _run_nmcli([
                "dev", "wifi", "hotspot",
                "ifname", "wlan0",
                "con-name", AP_CONNECTION_NAME,
                "ssid", ap_ssid,
                "band", "bg",
                "password", "",  # Open network
            ], timeout=20)

            if not success:
                # Some nmcli versions require a password for hotspot
                # Try with a minimal password
                logger.warning("Open hotspot failed, trying with password")
                success, output = _run_nmcli([
                    "dev", "wifi", "hotspot",
                    "ifname", "wlan0",
                    "con-name", AP_CONNECTION_NAME,
                    "ssid", ap_ssid,
                    "band", "bg",
                    "password", "dashpisetup",
                ], timeout=20)

            if success:
                self.state = STATE_AP_MODE
                logger.info("WiFi hotspot started: %s", ap_ssid)

                # Configure the hotspot IP to a known address
                # nmcli hotspot auto-assigns 10.42.0.1 by default
                # We'll use that instead of fighting it
                return True
            else:
                logger.error("Failed to start hotspot: %s", output)
                # Try to restore previous connection
                self._restore_wifi()
                return False

    def stop_ap_mode(self):
        """Stop the WiFi hotspot and restore the previous WiFi connection.

        Returns:
            True if hotspot stopped successfully, False otherwise.
        """
        if not self._is_pi:
            logger.info("[DEV] Would stop AP mode")
            self.state = STATE_DISCONNECTED
            return True

        with self._lock:
            logger.info("Stopping WiFi hotspot")

            # Deactivate hotspot
            _run_nmcli(["connection", "down", AP_CONNECTION_NAME], timeout=10)

            # Delete the hotspot connection profile
            _run_nmcli(["connection", "delete", AP_CONNECTION_NAME], timeout=10)

            # Restore previous WiFi
            self._restore_wifi()
            return True

    def connect(self, ssid, password=""):
        """Connect to a WiFi network with the given credentials.

        Stops AP mode if active, attempts connection, and verifies internet
        connectivity. Falls back to AP mode if connection fails.

        Args:
            ssid: WiFi network name to connect to.
            password: WiFi password (empty string for open networks).

        Returns:
            Tuple of (success: bool, message: str). On success, message
            contains the new IP address. On failure, message describes the error.
        """
        if not self._is_pi:
            logger.info("[DEV] Would connect to: %s", ssid)
            self.state = STATE_CONNECTED
            return True, "192.168.1.100"

        with self._lock:
            self.state = STATE_CONNECTING
            logger.info("Attempting to connect to WiFi: %s", ssid)

            # Stop AP mode if active
            _run_nmcli(["connection", "down", AP_CONNECTION_NAME], timeout=10)
            _run_nmcli(["connection", "delete", AP_CONNECTION_NAME], timeout=10)

            # Attempt connection
            connect_args = ["dev", "wifi", "connect", ssid]
            if password:
                connect_args.extend(["password", password])

            success, output = _run_nmcli(connect_args, timeout=30)

            if not success:
                logger.error("WiFi connection failed: %s", output)
                self.state = STATE_DISCONNECTED
                return False, f"Connection failed: {output}"

            # Wait for connectivity with polling
            for attempt in range(8):  # Up to ~16 seconds
                time.sleep(2)
                if self.check_connectivity():
                    ip = self.get_ip_address()
                    self.state = STATE_CONNECTED
                    logger.info("WiFi connected: %s (IP: %s)", ssid, ip)
                    return True, ip or "connected"

            # Connection established but no internet
            logger.warning("WiFi associated but no internet connectivity")
            self.state = STATE_CONNECTED  # Still connected, just no internet
            ip = self.get_ip_address()
            if ip:
                return True, ip
            else:
                self.state = STATE_DISCONNECTED
                return False, "Connected to WiFi but could not get IP address"

    def get_hotspot_ip(self):
        """Get the IP address of the hotspot interface.

        nmcli hotspot typically uses 10.42.0.1 by default.
        """
        if not self._is_pi:
            return "10.42.0.1"

        try:
            result = subprocess.run(
                ["ip", "-4", "addr", "show", "wlan0"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("inet "):
                    # "inet 10.42.0.1/24 ..."
                    return line.split()[1].split("/")[0]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return "10.42.0.1"

    def _get_active_wifi_connection(self):
        """Get the name of the currently active WiFi connection, if any."""
        success, output = _run_nmcli(
            ["-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"]
        )
        if not success:
            return None
        for line in output.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[1] == "802-11-wireless" and parts[2] == "wlan0":
                return parts[0]
        return None

    def _restore_wifi(self):
        """Attempt to restore the previous WiFi connection."""
        if self._previous_connection:
            logger.info("Restoring WiFi connection: %s", self._previous_connection)
            success, _ = _run_nmcli(
                ["connection", "up", self._previous_connection], timeout=20
            )
            if success:
                self.state = STATE_CONNECTED
                return True
            else:
                logger.warning("Failed to restore %s, trying auto-connect",
                               self._previous_connection)

        # Fall back to letting NetworkManager auto-connect
        logger.info("Enabling WiFi auto-connect")
        _run_nmcli(["radio", "wifi", "off"], timeout=5)
        time.sleep(1)
        _run_nmcli(["radio", "wifi", "on"], timeout=5)

        # Wait for auto-connect
        for _ in range(5):
            time.sleep(3)
            if self.check_connectivity():
                self.state = STATE_CONNECTED
                return True

        self.state = STATE_DISCONNECTED
        return False
