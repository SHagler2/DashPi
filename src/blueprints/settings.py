"""Settings blueprint — device config, OTA updates, shutdown/reboot, log download."""

from flask import Blueprint, request, jsonify, current_app, render_template, Response
from datetime import datetime, timedelta
import os
import subprocess
import pytz
import logging
import io
import json

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
    return render_template('settings.html', device_settings=device_config.get_config(), timezones=timezones)

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
            }
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
            hours = int(hours_str)
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
        return Response(f"Error reading logs: {e}", status=500, mimetype="text/plain")

