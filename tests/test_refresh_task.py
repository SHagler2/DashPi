"""Tests for the critical path: RefreshTask + action classes + display pipeline.

Coverage:
  - ManualRefresh / AutoRefresh / LoopRefresh action class unit tests
  - End-to-end smoke test: manual_update → Clock plugin → display_manager.display_image()
"""

import os
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from PIL import Image

from refresh_task import RefreshTask, ManualRefresh, AutoRefresh, LoopRefresh
from model import Loop, PluginReference


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_plugin():
    """A plugin that returns a valid 800x480 PIL image."""
    plugin = MagicMock()
    plugin.config = {"image_settings": []}
    plugin.generate_image.return_value = Image.new("RGB", (800, 480), "navy")
    return plugin


@pytest.fixture
def task_config(tmp_path):
    """A device_config mock rich enough to run RefreshTask."""
    cfg = MagicMock()
    cfg.get_resolution.return_value = (800, 480)
    cfg.current_image_file = str(tmp_path / "current.jpg")
    cfg.plugin_image_dir = str(tmp_path / "plugins")
    os.makedirs(cfg.plugin_image_dir, exist_ok=True)

    # Blank current image so the task doesn't fail on first load
    Image.new("RGB", (800, 480), "black").save(cfg.current_image_file)

    def config_side_effect(key=None, default=None):
        values = {
            "orientation": "horizontal",
            "timezone": "UTC",
            "loop_enabled": True,
            "display_type": "mock",
            "show_plugin_icon": False,
            "auto_refresh_tracking": {},
        }
        if key is None:
            return dict(values)
        return values.get(key, default)

    cfg.get_config.side_effect = config_side_effect
    cfg.get_loop_override.return_value = None
    cfg.get_plugin.return_value = None

    loop_mgr = MagicMock()
    loop_mgr.loops = []
    loop_mgr.rotation_interval_seconds = 300
    loop_mgr.determine_active_loop.return_value = None
    cfg.get_loop_manager.return_value = loop_mgr

    refresh_info = MagicMock()
    refresh_info.plugin_id = None
    refresh_info.image_hash = None
    cfg.get_refresh_info.return_value = refresh_info

    return cfg


@pytest.fixture
def mock_display():
    """A display_manager mock that records display_image calls."""
    display = MagicMock()
    display._display_blanked = False
    display.supports_fast_refresh.return_value = True
    return display


# ---------------------------------------------------------------------------
# Action class unit tests — no threading required
# ---------------------------------------------------------------------------

class TestManualRefresh:
    def test_execute_calls_generate_image(self, mock_plugin, task_config):
        action = ManualRefresh("clock", {"face": "digital"})
        result = action.execute(mock_plugin, task_config, datetime.now(timezone.utc))

        mock_plugin.generate_image.assert_called_once_with({"face": "digital"}, task_config)
        assert isinstance(result, Image.Image)

    def test_metadata(self):
        action = ManualRefresh("weather", {"units": "imperial"})
        assert action.get_plugin_id() == "weather"
        assert action.get_refresh_info()["refresh_type"] == "Manual Update"
        assert action.get_refresh_info()["plugin_id"] == "weather"


class TestAutoRefresh:
    def test_execute_calls_generate_image(self, mock_plugin, task_config):
        action = AutoRefresh("stocks", {"tickers": "AAPL"})
        result = action.execute(mock_plugin, task_config, datetime.now(timezone.utc))

        mock_plugin.generate_image.assert_called_once_with({"tickers": "AAPL"}, task_config)
        assert isinstance(result, Image.Image)

    def test_metadata(self):
        action = AutoRefresh("stocks", {})
        assert action.get_plugin_id() == "stocks"
        assert action.get_refresh_info()["refresh_type"] == "Auto Refresh"

    def test_none_settings_becomes_empty_dict(self, mock_plugin, task_config):
        """None settings should not crash generate_image call."""
        action = AutoRefresh("clock", None)
        action.execute(mock_plugin, task_config, datetime.now(timezone.utc))
        mock_plugin.generate_image.assert_called_once_with({}, task_config)


class TestLoopRefresh:
    def test_metadata(self):
        loop = MagicMock()
        loop.name = "Morning"
        plugin_ref = MagicMock()
        plugin_ref.plugin_id = "clock"

        action = LoopRefresh(loop, plugin_ref)
        assert action.get_plugin_id() == "clock"
        info = action.get_refresh_info()
        assert info["refresh_type"] == "Loop"
        assert info["loop"] == "Morning"
        assert info["plugin_id"] == "clock"

    def test_execute_generates_and_caches_image(self, mock_plugin, task_config, tmp_path):
        loop = MagicMock()
        loop.name = "Default"
        plugin_ref = MagicMock()
        plugin_ref.plugin_id = "clock"
        plugin_ref.plugin_settings = {"face": "analog"}
        plugin_ref.should_refresh.return_value = True

        action = LoopRefresh(loop, plugin_ref)
        result = action.execute(mock_plugin, task_config, datetime.now(timezone.utc))

        mock_plugin.generate_image.assert_called_once_with({"face": "analog"}, task_config)
        assert isinstance(result, Image.Image)

        # Cached JPEG should be written to plugin_image_dir
        cache_path = os.path.join(task_config.plugin_image_dir, "loop_clock.jpg")
        assert os.path.exists(cache_path)

    def test_execute_uses_cache_when_fresh(self, mock_plugin, task_config, tmp_path):
        """If should_refresh() is False and a cached image exists, generate_image is NOT called."""
        loop = MagicMock()
        loop.name = "Default"
        plugin_ref = MagicMock()
        plugin_ref.plugin_id = "clock"
        plugin_ref.plugin_settings = {}
        plugin_ref.should_refresh.return_value = False

        # Pre-write a cached image
        cache_path = os.path.join(task_config.plugin_image_dir, "loop_clock.jpg")
        Image.new("RGB", (800, 480), "green").save(cache_path, "JPEG")

        action = LoopRefresh(loop, plugin_ref)
        result = action.execute(mock_plugin, task_config, datetime.now(timezone.utc))

        mock_plugin.generate_image.assert_not_called()
        assert isinstance(result, Image.Image)


# ---------------------------------------------------------------------------
# End-to-end smoke test: the critical path through RefreshTask
# ---------------------------------------------------------------------------

class TestRefreshTaskCriticalPath:
    """
    Verifies that the full path works:
      manual_update(ManualRefresh) → _run picks it up → execute() → display_manager.display_image()

    Uses the real Clock plugin (no network, pure PIL render) and a mock display.
    Plugin registry must be pre-loaded so get_plugin_instance("clock") succeeds.
    """

    @pytest.fixture
    def clock_plugin_config(self):
        """Minimal plugin config dict matching what device_config.get_plugin() returns."""
        return {
            "id": "clock",
            "display_name": "Clock",
            "class": "Clock",
            "image_settings": [],
        }

    def test_manual_update_clock_reaches_display(self, task_config, mock_display, clock_plugin_config):
        from plugins.plugin_registry import load_plugins, PLUGIN_CLASSES

        # Ensure Clock is registered
        if "clock" not in PLUGIN_CLASSES:
            load_plugins([clock_plugin_config])

        # Wire config to return clock plugin config for get_plugin("clock")
        task_config.get_plugin.side_effect = lambda pid: clock_plugin_config if pid == "clock" else None

        task = RefreshTask(task_config, mock_display)

        # Suppress filesystem status writes
        task._set_global_status = MagicMock()
        task._stop_splash_if_needed = MagicMock()

        task.start()
        try:
            action = ManualRefresh("clock", {"face": "digital", "showTitle": "false"})
            task.manual_update(action)

            mock_display.display_image.assert_called_once()
            image_arg = mock_display.display_image.call_args[0][0]
            assert isinstance(image_arg, Image.Image)
            assert image_arg.size[0] > 0 and image_arg.size[1] > 0
        finally:
            task.stop()

    def test_manual_update_plugin_not_found_does_not_crash(self, task_config, mock_display):
        """If plugin config is missing, _run logs an error but doesn't crash or hang."""
        task_config.get_plugin.return_value = None  # simulate unconfigured plugin

        task = RefreshTask(task_config, mock_display)
        task._set_global_status = MagicMock()
        task._stop_splash_if_needed = MagicMock()

        task.start()
        try:
            action = ManualRefresh("nonexistent_plugin", {})
            task.manual_update(action)  # should return without crashing
            mock_display.display_image.assert_not_called()
        finally:
            task.stop()
