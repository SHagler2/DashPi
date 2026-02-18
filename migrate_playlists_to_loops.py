#!/usr/bin/env python3
"""
Migration script to convert playlist configurations to loop configurations.

This script:
1. Reads device.json
2. Converts any existing playlist_config to loop_config
3. Removes display_mode setting
4. Writes the updated configuration back to device.json

Run this script once before deploying the new DashPi version.
"""

import json
import sys
import os
from pathlib import Path

def migrate_playlist_to_loop(playlist_data):
    """Convert a playlist dict to a loop dict."""
    loop = {
        "name": playlist_data["name"],
        "start_time": playlist_data["start_time"],
        "end_time": playlist_data["end_time"],
        "plugin_order": [],
        "current_plugin_index": playlist_data.get("current_plugin_index")
    }

    # Convert plugin instances to plugin references
    for plugin_instance in playlist_data.get("plugins", []):
        plugin_ref = {
            "plugin_id": plugin_instance["plugin_id"],
            "plugin_settings": plugin_instance.get("plugin_settings", {}),
            "latest_refresh_time": plugin_instance.get("latest_refresh_time")
        }

        # Convert refresh settings to refresh_interval_seconds
        refresh = plugin_instance.get("refresh", {})
        if "interval" in refresh:
            plugin_ref["refresh_interval_seconds"] = refresh["interval"]
        elif "scheduled" in refresh:
            # If scheduled refresh, default to 24 hours (daily)
            plugin_ref["refresh_interval_seconds"] = 86400
        else:
            # Default to 30 minutes
            plugin_ref["refresh_interval_seconds"] = 1800

        loop["plugin_order"].append(plugin_ref)

    return loop

def migrate_config(config_path):
    """Migrate a device config file from playlists to loops."""
    print(f"Reading config from: {config_path}")

    # Read existing config
    with open(config_path, 'r') as f:
        config = json.load(f)

    # Check if migration is needed
    if "playlist_config" not in config and "display_mode" not in config:
        print("✓ Config already migrated (no playlist_config or display_mode found)")
        return False

    # Backup original config
    backup_path = config_path.with_suffix('.json.backup')
    print(f"Creating backup at: {backup_path}")
    with open(backup_path, 'w') as f:
        json.dump(config, f, indent=4)

    # Migrate playlist_config to loop_config
    if "playlist_config" in config:
        print("Migrating playlist_config to loop_config...")
        playlist_config = config["playlist_config"]

        loops = []
        for playlist in playlist_config.get("playlists", []):
            loop = migrate_playlist_to_loop(playlist)
            loops.append(loop)
            print(f"  - Converted playlist '{playlist['name']}' with {len(playlist.get('plugins', []))} plugins")

        # Create loop_config
        loop_config = {
            "loops": loops,
            "rotation_interval_seconds": config.get("plugin_cycle_interval_seconds", 300),
            "active_loop": playlist_config.get("active_playlist")
        }

        config["loop_config"] = loop_config
        del config["playlist_config"]
        print(f"✓ Migrated {len(loops)} playlist(s) to loop(s)")

    # Ensure loop_config exists (even if there was no playlist_config)
    if "loop_config" not in config:
        print("Creating default loop_config...")
        config["loop_config"] = {
            "loops": [
                {
                    "name": "Default",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugin_order": [],
                    "current_plugin_index": None
                }
            ],
            "rotation_interval_seconds": config.get("plugin_cycle_interval_seconds", 300),
            "active_loop": None
        }

    # Remove display_mode if present
    if "display_mode" in config:
        print(f"Removing display_mode (was: {config['display_mode']})")
        del config["display_mode"]

    # Clean up refresh_info (remove playlist/plugin_instance fields)
    if "refresh_info" in config:
        refresh_info = config["refresh_info"]
        if "playlist" in refresh_info:
            del refresh_info["playlist"]
            print("  - Removed 'playlist' from refresh_info")
        if "plugin_instance" in refresh_info:
            del refresh_info["plugin_instance"]
            print("  - Removed 'plugin_instance' from refresh_info")

    # Write updated config
    print(f"Writing migrated config to: {config_path}")
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=4)

    print("✓ Migration complete!")
    return True

def main():
    """Main migration entry point."""
    # Default to production config path
    default_config = Path(__file__).parent / "src" / "config" / "device.json"

    # Allow override via command line
    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
    else:
        config_path = default_config

    if not config_path.exists():
        print(f"Error: Config file not found at {config_path}")
        print("Usage: python migrate_playlists_to_loops.py [path/to/device.json]")
        sys.exit(1)

    print("=" * 60)
    print("DashPi Playlist → Loop Migration")
    print("=" * 60)

    try:
        migrated = migrate_config(config_path)

        if migrated:
            print("\n✓ Migration successful!")
            print(f"  - Original config backed up to: {config_path.with_suffix('.json.backup')}")
            print(f"  - Updated config written to: {config_path}")
        else:
            print("\nNo migration needed.")

        print("\nNext steps:")
        print("1. Review the migrated config at:", config_path)
        print("2. Deploy the updated DashPi code")
        print("3. Restart the dashpi service")

    except Exception as e:
        print(f"\n✗ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
