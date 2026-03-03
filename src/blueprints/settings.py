"""Settings blueprint — device config, OTA updates, shutdown/reboot, log download, config backup/restore."""

from flask import Blueprint, request, jsonify, current_app, render_template, Response, send_file
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import os
import subprocess
import pytz
import logging
import io
import json
import zipfile
import shutil

# Try to import cysystemd for journal reading (Linux only)
try:
    from cysystemd.reader import JournalReader, JournalOpenMode, Rule
    JOURNAL_AVAILABLE = True
except ImportError:
    JOURNAL_AVAILABLE = False
    # Define dummy classes for when cysystemd is not available
    class JournalOpenMode:
        SYSTEM = None
    class Rule:
        pass
    class JournalReader:
        def __init__(self, *args, **kwargs):
            pass


logger = logging.getLogger(__name__)
settings_bp = Blueprint("settings", __name__)

def _get_version():
    """Read version from VERSION file."""
    version_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'VERSION')
    try:
        with open(version_file, 'r') as f:
            return f.read().strip()
    except Exception:
        return "?"

@settings_bp.route('/settings')
def settings_page():
    """Render device settings page (display, timezone, brightness schedule)."""
    device_config = current_app.config['DEVICE_CONFIG']
    timezones = sorted(pytz.all_timezones_set)

    # Get WiFi info for display
    wifi_manager = current_app.config.get('WIFI_MANAGER')
    wifi_ssid = wifi_manager.get_wifi_ssid() if wifi_manager else None
    wifi_ip = wifi_manager.get_ip_address() if wifi_manager else None

    return render_template('settings.html', device_settings=device_config.get_config(),
                           timezones=timezones, wifi_ssid=wifi_ssid, wifi_ip=wifi_ip)

@settings_bp.route('/save_settings', methods=['POST'])
def save_settings():
    """Save device settings from the settings form."""
    device_config = current_app.config['DEVICE_CONFIG']

    try:
        form_data = request.form.to_dict()

        time_format = form_data.get("timeFormat")
        if not form_data.get("timezoneName"):
            return jsonify({"error": "Time Zone is required"}), 400
        if not time_format or time_format not in ["12h", "24h"]:
            return jsonify({"error": "Time format is required"}), 400

        # Build image settings — include inky_saturation for e-ink displays
        image_settings = {
            "saturation": float(form_data.get("saturation", "1.0")),
            "sharpness": float(form_data.get("sharpness", "1.0")),
            "contrast": float(form_data.get("contrast", "1.0"))
        }
        if "inkySaturation" in form_data:
            image_settings["inky_saturation"] = float(form_data.get("inkySaturation", "0.5"))

        settings = {
            "device_name": form_data.get("deviceName", "").strip() or None,
            "orientation": form_data.get("orientation"),
            "inverted_image": form_data.get("invertImage"),
            "log_system_stats": form_data.get("logSystemStats"),
            "show_plugin_icon": form_data.get("showPluginIcon"),
            "timezone": form_data.get("timezoneName"),
            "time_format": form_data.get("timeFormat"),
            "image_settings": image_settings,
            "brightness_schedule": {
                "enabled": "brightnessScheduleEnabled" in form_data,
                "day_brightness": float(form_data.get("dayBrightness", "1.0")),
                "evening_brightness": float(form_data.get("eveningBrightness", "0.6")),
                "night_brightness": float(form_data.get("nightBrightness", "0.3")),
                "day_start": form_data.get("dayStart", "07:00"),
                "evening_start": form_data.get("eveningStart", "18:00"),
                "night_start": form_data.get("nightStart", "22:00"),
            },
            "display_transitions": {
                "enabled": "displayTransitions" in form_data,
                "steps": 10,
                "duration_ms": 800,
            },
        }
        # Remove None device_name to keep existing value
        if settings["device_name"] is None:
            del settings["device_name"]
        device_config.update_config(settings)

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500
    return jsonify({"success": True, "message": "Saved settings."})

@settings_bp.route('/shutdown', methods=['POST'])
def shutdown():
    """Shutdown or reboot the Pi. Send {"reboot": true} for reboot."""
    data = request.get_json() or {}
    try:
        if data.get("reboot"):
            logger.info("Reboot requested")
            subprocess.run(["sudo", "reboot"], check=True, timeout=10)
        else:
            logger.info("Shutdown requested")
            subprocess.run(["sudo", "shutdown", "-h", "now"], check=True, timeout=10)
    except subprocess.SubprocessError as e:
        logger.error(f"Shutdown/reboot failed: {e}")
        return jsonify({"error": "Failed to execute shutdown command"}), 500
    return jsonify({"success": True})

@settings_bp.route('/api/update/check', methods=['GET'])
def check_for_updates():
    """Check if there are updates available on the remote repository."""
    try:
        repo_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # Read current local version
        version_file = os.path.join(repo_dir, 'VERSION')
        local_version = '?'
        if os.path.isfile(version_file):
            with open(version_file, 'r') as f:
                local_version = f.read().strip()

        # Detect current branch
        branch_result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            cwd=repo_dir, capture_output=True, text=True, timeout=10
        )
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else 'main'

        # Fetch latest from remote (non-destructive)
        result = subprocess.run(
            ['git', 'fetch', 'origin', branch],
            cwd=repo_dir, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return jsonify({
                "error": f"Git fetch failed: {result.stderr.strip()}",
                "local_version": local_version
            }), 500

        remote_ref = f'origin/{branch}'

        # Compare local HEAD with remote
        local_hash = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=repo_dir, capture_output=True, text=True, timeout=10
        ).stdout.strip()

        remote_hash = subprocess.run(
            ['git', 'rev-parse', remote_ref],
            cwd=repo_dir, capture_output=True, text=True, timeout=10
        ).stdout.strip()

        # Read remote version
        remote_version_result = subprocess.run(
            ['git', 'show', f'{remote_ref}:VERSION'],
            cwd=repo_dir, capture_output=True, text=True, timeout=10
        )
        remote_version = remote_version_result.stdout.strip() if remote_version_result.returncode == 0 else '?'

        # Count commits behind
        behind_result = subprocess.run(
            ['git', 'rev-list', '--count', f'HEAD..{remote_ref}'],
            cwd=repo_dir, capture_output=True, text=True, timeout=10
        )
        commits_behind = int(behind_result.stdout.strip()) if behind_result.returncode == 0 else 0

        # Get commit log of what's new
        changelog = []
        if commits_behind > 0:
            log_result = subprocess.run(
                ['git', 'log', '--oneline', f'HEAD..{remote_ref}', '--max-count=20'],
                cwd=repo_dir, capture_output=True, text=True, timeout=10
            )
            if log_result.returncode == 0:
                changelog = [line.strip() for line in log_result.stdout.strip().split('\n') if line.strip()]

        return jsonify({
            "update_available": local_hash != remote_hash,
            "local_version": local_version,
            "remote_version": remote_version,
            "commits_behind": commits_behind,
            "changelog": changelog,
            "local_hash": local_hash[:8],
            "remote_hash": remote_hash[:8],
            "branch": branch
        })

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Git operation timed out"}), 500
    except Exception as e:
        logger.error(f"Update check failed: {e}")
        return jsonify({"error": str(e)}), 500


@settings_bp.route('/api/update/apply', methods=['POST'])
def apply_update():
    """Pull latest code from remote and restart the service."""
    try:
        repo_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # Detect current branch
        branch_result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            cwd=repo_dir, capture_output=True, text=True, timeout=10
        )
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else 'main'

        # Stash any local changes (e.g., __pycache__, config edits)
        subprocess.run(
            ['git', 'stash', '--include-untracked'],
            cwd=repo_dir, capture_output=True, text=True, timeout=15
        )

        # Pull latest from current branch
        result = subprocess.run(
            ['git', 'reset', '--hard', f'origin/{branch}'],
            cwd=repo_dir, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return jsonify({"error": f"Git reset failed: {result.stderr.strip()}"}), 500

        # Read the new version
        version_file = os.path.join(repo_dir, 'VERSION')
        new_version = '?'
        if os.path.isfile(version_file):
            with open(version_file, 'r') as f:
                new_version = f.read().strip()

        # Schedule a service restart (delayed so this response can be sent first)
        subprocess.Popen(
            ['bash', '-c', 'sleep 2 && sudo systemctl restart dashpi 2>/dev/null || sudo systemctl restart inkypi 2>/dev/null'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        return jsonify({
            "success": True,
            "new_version": new_version,
            "message": "Update applied. Service restarting..."
        })

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Git operation timed out"}), 500
    except Exception as e:
        logger.error(f"Update apply failed: {e}")
        return jsonify({"error": str(e)}), 500


@settings_bp.route('/download-logs')
def download_logs():
    """Download service logs as a text file. Reads from systemd journal."""
    try:
        buffer = io.StringIO()
        
        # Get 'hours' from query parameters, default to 2 if not provided or invalid
        hours_str = request.args.get('hours', '2')
        try:
            hours = min(max(int(hours_str), 1), 168)  # Clamp 1 hour to 1 week
        except ValueError:
            hours = 2
        since = datetime.now() - timedelta(hours=hours)

        if not JOURNAL_AVAILABLE:
            # Return a message when running in development mode without systemd
            buffer.write(f"Log download not available in development mode (cysystemd not installed).\n")
            buffer.write(f"Logs would normally show DashPi service logs from the last {hours} hours.\n")
            buffer.write(f"\nTo see Flask development logs, check your terminal output.\n")
        else:
            reader = JournalReader()
            reader.open(JournalOpenMode.SYSTEM)
            # Match either service name (dashpi or inkypi) for backwards compatibility
            reader.add_filter(Rule("_SYSTEMD_UNIT", "dashpi.service"))
            reader.add_filter(Rule("_SYSTEMD_UNIT", "inkypi.service"))
            reader.seek_realtime_usec(int(since.timestamp() * 1_000_000))

            for record in reader:
                try:
                    ts = datetime.fromtimestamp(record.get_realtime_usec() / 1_000_000)
                    formatted_ts = ts.strftime("%b %d %H:%M:%S")
                except Exception:
                    formatted_ts = "??? ?? ??:??:??"

                data = record.data
                hostname = data.get("_HOSTNAME", "unknown-host")
                identifier = data.get("SYSLOG_IDENTIFIER") or data.get("_COMM", "?")
                pid = data.get("_PID", "?")
                msg = data.get("MESSAGE", "").rstrip()

                # Format the log entry similar to the journalctl default output
                buffer.write(f"{formatted_ts} {hostname} {identifier}[{pid}]: {msg}\n")

        buffer.seek(0)
        # Add date and time to the filename
        now_str = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"dashpi_{now_str}.log"
        return Response(
            buffer.read(),
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        logger.error(f"Error reading logs: {e}")
        return Response("Error reading logs", status=500, mimetype="text/plain")


@settings_bp.route('/api/config/export')
def export_config():
    """Export device configuration as a ZIP archive.

    Query params:
        include_env: Include .env API keys (default false)
        include_images: Include saved user images (default false)
    """
    try:
        device_config = current_app.config['DEVICE_CONFIG']
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        include_env = request.args.get('include_env', 'false').lower() == 'true'
        include_images = request.args.get('include_images', 'false').lower() == 'true'

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Always include device.json (exclude transient state)
            config = device_config.get_config().copy()
            config.pop('refresh_info', None)
            config.pop('loop_override', None)
            zf.writestr('device.json', json.dumps(config, indent=2))

            # Optionally include .env
            if include_env:
                env_path = os.path.join(base_dir, '.env')
                if os.path.isfile(env_path):
                    zf.write(env_path, '.env')

            # Optionally include saved images (skip dotfiles and non-image files)
            if include_images:
                image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}
                saved_dir = os.path.join(base_dir, 'src', 'static', 'images', 'saved')
                if os.path.isdir(saved_dir):
                    for fname in os.listdir(saved_dir):
                        if fname.startswith('.'):
                            continue
                        if os.path.splitext(fname)[1].lower() not in image_extensions:
                            continue
                        fpath = os.path.join(saved_dir, fname)
                        if os.path.isfile(fpath):
                            zf.write(fpath, f'saved_images/{fname}')

        buffer.seek(0)
        now_str = datetime.now().strftime("%Y-%m-%d")
        device_name = device_config.get_config().get('device_name') or 'DashPi'
        version = _get_version()
        filename = f"{device_name}-DashPi V{version}-{now_str}.zip"
        return send_file(
            buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"Config export failed: {e}")
        return jsonify({"error": f"Export failed: {e}"}), 500


@settings_bp.route('/api/config/import', methods=['POST'])
def import_config():
    """Import device configuration from a previously exported ZIP archive.

    Validates the ZIP contents, backs up current config, then applies.
    Returns JSON with restart_required flag.
    """
    try:
        device_config = current_app.config['DEVICE_CONFIG']
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        file = request.files.get('file')
        if not file or not file.filename:
            return jsonify({"error": "No file uploaded"}), 400

        # Validate file extension
        if not file.filename.lower().endswith('.zip'):
            return jsonify({"error": "File must be a .zip archive"}), 400

        # Read ZIP into memory
        zip_data = io.BytesIO(file.read())
        if not zipfile.is_zipfile(zip_data):
            return jsonify({"error": "File is not a valid ZIP archive"}), 400

        zip_data.seek(0)
        with zipfile.ZipFile(zip_data, 'r') as zf:
            names = zf.namelist()

            # Must contain device.json
            if 'device.json' not in names:
                return jsonify({"error": "ZIP must contain device.json"}), 400

            # Validate device.json
            try:
                config_data = json.loads(zf.read('device.json'))
            except (json.JSONDecodeError, ValueError) as e:
                return jsonify({"error": f"Invalid device.json: {e}"}), 400

            if not isinstance(config_data, dict):
                return jsonify({"error": "device.json must be a JSON object"}), 400

            # Check for expected keys (at least orientation should exist)
            if 'orientation' not in config_data:
                return jsonify({"error": "device.json missing required fields"}), 400

            # Validate .env if present
            has_env = '.env' in names
            if has_env:
                env_content = zf.read('.env').decode('utf-8', errors='ignore')
                for line in env_content.strip().splitlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' not in line:
                        return jsonify({"error": f"Invalid .env line: {line[:50]}"}), 400

            # Validate images if present
            image_files = [n for n in names if n.startswith('saved_images/') and not n.endswith('/')]
            for img_name in image_files:
                img_data = zf.read(img_name)
                try:
                    from PIL import Image
                    img = Image.open(io.BytesIO(img_data))
                    img.verify()
                except Exception:
                    return jsonify({"error": f"Invalid image: {os.path.basename(img_name)}"}), 400

            # --- All validation passed, apply changes ---

            # Backup current device.json
            config_path = device_config.config_file
            backup_path = config_path + '.bak'
            if os.path.isfile(config_path):
                shutil.copy2(config_path, backup_path)
                logger.info(f"Backed up config to {backup_path}")

            # Apply device.json
            device_config.update_config(config_data)
            logger.info("Imported device.json")

            # Apply .env if present
            if has_env:
                env_path = os.path.join(base_dir, '.env')
                env_backup = env_path + '.bak'
                if os.path.isfile(env_path):
                    shutil.copy2(env_path, env_backup)
                with open(env_path, 'w') as f:
                    f.write(env_content)
                logger.info("Imported .env")

            # Apply saved images if present
            if image_files:
                saved_dir = os.path.join(base_dir, 'src', 'static', 'images', 'saved')
                os.makedirs(saved_dir, exist_ok=True)
                for img_name in image_files:
                    fname = secure_filename(os.path.basename(img_name))
                    if fname:
                        with open(os.path.join(saved_dir, fname), 'wb') as f:
                            f.write(zf.read(img_name))
                logger.info(f"Imported {len(image_files)} saved image(s)")

        summary = "Restored: device.json"
        if has_env:
            summary += ", API keys"
        if image_files:
            summary += f", {len(image_files)} image(s)"

        return jsonify({
            "success": True,
            "message": summary + ". Restart required for changes to take effect.",
            "restart_required": True,
            "restored_env": has_env,
            "restored_images": len(image_files)
        })

    except Exception as e:
        logger.error(f"Config import failed: {e}")
        return jsonify({"error": f"Import failed: {e}"}), 500

