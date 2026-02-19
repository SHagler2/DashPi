"""Tests for Config class: round-trip read/write, resolution, plugins, loops."""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from config import Config
from model import LoopManager, RefreshInfo


@pytest.fixture
def config_dir(tmp_path):
    """Create a temp config directory with a valid device.json."""
    config_subdir = tmp_path / "config"
    config_subdir.mkdir()

    device_json = {
        "name": "TestPi",
        "display_type": "mock",
        "resolution": [1024, 600],
        "orientation": "horizontal",
        "timezone": "US/Central",
        "time_format": "12h",
        "loop_config": {
            "loops": [
                {
                    "name": "Default",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugin_order": [],
                    "current_plugin_index": None,
                }
            ],
            "rotation_interval_seconds": 300,
            "active_loop": None,
        },
        "refresh_info": {
            "refresh_time": None,
            "image_hash": None,
            "refresh_type": None,
            "plugin_id": None,
        },
    }
    config_file = config_subdir / "device.json"
    config_file.write_text(json.dumps(device_json, indent=4))

    # Create minimal plugins directory with one plugin
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    clock_dir = plugins_dir / "clock"
    clock_dir.mkdir()
    (clock_dir / "plugin-info.json").write_text(
        json.dumps({"id": "clock", "display_name": "Clock", "class": "Clock"})
    )

    return tmp_path


@pytest.fixture
def cfg(config_dir):
    """Create a real Config instance pointing at our temp directory."""
    Config.BASE_DIR = str(config_dir)
    Config.config_file = str(config_dir / "config" / "device.json")
    Config.current_image_file = str(config_dir / "current_image.png")
    Config.plugin_image_dir = str(config_dir / "plugin_images")

    with patch("config.load_dotenv"):
        return Config()


class TestConfigReadWrite:
    def test_read_config_returns_dict(self, cfg):
        assert isinstance(cfg.config, dict)
        assert cfg.config["name"] == "TestPi"

    def test_get_resolution(self, cfg):
        assert cfg.get_resolution() == (1024, 600)

    def test_get_config_key(self, cfg):
        assert cfg.get_config("orientation") == "horizontal"

    def test_get_config_missing_key_default(self, cfg):
        assert cfg.get_config("nonexistent", default="fallback") == "fallback"

    def test_get_config_missing_key_no_default(self, cfg):
        result = cfg.get_config("nonexistent")
        assert result == {}

    def test_get_config_no_key_returns_full(self, cfg):
        full = cfg.get_config()
        assert isinstance(full, dict)
        assert "name" in full

    def test_write_and_reread(self, cfg):
        cfg.update_value("name", "ModifiedPi")
        cfg.write_config()

        # Re-read from disk
        with open(cfg.config_file) as f:
            data = json.load(f)
        assert data["name"] == "ModifiedPi"

    def test_update_config_round_trip(self, cfg):
        cfg.update_config({"timezone": "US/Eastern"})

        # Re-read from disk
        with open(cfg.config_file) as f:
            data = json.load(f)
        assert data["timezone"] == "US/Eastern"


class TestConfigPlugins:
    def test_plugins_list_loaded(self, cfg):
        plugins = cfg.plugins_list
        assert len(plugins) == 1
        assert plugins[0]["id"] == "clock"

    def test_get_plugins_no_order(self, cfg):
        ordered = cfg.get_plugins()
        assert ordered[0]["id"] == "clock"

    def test_get_plugins_with_order(self, cfg):
        cfg.config["plugin_order"] = ["clock"]
        ordered = cfg.get_plugins()
        assert ordered[0]["id"] == "clock"

    def test_get_plugin_found(self, cfg):
        plugin = cfg.get_plugin("clock")
        assert plugin is not None
        assert plugin["id"] == "clock"

    def test_get_plugin_not_found(self, cfg):
        assert cfg.get_plugin("nonexistent") is None

    def test_set_plugin_order(self, cfg):
        cfg.set_plugin_order(["clock"])
        assert cfg.config["plugin_order"] == ["clock"]


class TestConfigLoopManager:
    def test_loop_manager_loaded(self, cfg):
        lm = cfg.loop_manager
        assert isinstance(lm, LoopManager)
        assert len(lm.loops) == 1
        assert lm.loops[0].name == "Default"

    def test_loop_manager_rotation_interval(self, cfg):
        assert cfg.loop_manager.rotation_interval_seconds == 300

    def test_get_loop_manager(self, cfg):
        lm = cfg.get_loop_manager()
        assert lm is cfg.loop_manager


class TestConfigRefreshInfo:
    def test_refresh_info_loaded(self, cfg):
        ri = cfg.refresh_info
        assert isinstance(ri, RefreshInfo)
        assert ri.refresh_time is None

    def test_get_refresh_info(self, cfg):
        ri = cfg.get_refresh_info()
        assert ri is cfg.refresh_info


class TestConfigEnv:
    def test_load_env_key_returns_none(self, cfg):
        # Env var shouldn't exist in test context
        assert cfg.load_env_key("DASHPI_TEST_NONEXISTENT_KEY") is None

    def test_load_env_key_returns_value(self, cfg):
        os.environ["DASHPI_TEST_KEY_XYZ"] = "test_value"
        try:
            assert cfg.load_env_key("DASHPI_TEST_KEY_XYZ") == "test_value"
        finally:
            del os.environ["DASHPI_TEST_KEY_XYZ"]


class TestUpdateValue:
    def test_update_without_write(self, cfg):
        cfg.update_value("test_key", "test_val")
        assert cfg.config["test_key"] == "test_val"

    def test_update_with_write(self, cfg):
        cfg.update_value("test_key2", "test_val2", write=True)
        with open(cfg.config_file) as f:
            data = json.load(f)
        assert data["test_key2"] == "test_val2"
