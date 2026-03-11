"""Tests for the critical path: RefreshTask + action classes + display pipeline.

Coverage:
  - ManualRefresh / AutoRefresh / LoopRefresh action class unit tests
  - End-to-end smoke test: manual_update → Clock plugin → display_manager.display_image()
  - _compute_sleep_time: sleep interval, auto-refresh, blanked-display, first-run
  - _determine_refresh_action: manual priority, loop rotation, auto-refresh, standalone
  - _execute_refresh_action: plugin-not-found error, plugin-returns-None, display called
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


# ---------------------------------------------------------------------------
# _compute_sleep_time unit tests
# ---------------------------------------------------------------------------

class TestComputeSleepTime:
    def _make_task(self, task_config, mock_display):
        task = RefreshTask(task_config, mock_display)
        task._set_global_status = MagicMock()
        task._stop_splash_if_needed = MagicMock()
        return task

    def test_returns_loop_interval_by_default(self, task_config, mock_display):
        task = self._make_task(task_config, mock_display)
        task.first_run = False
        loop_mgr = task_config.get_loop_manager()
        loop_mgr.rotation_interval_seconds = 300
        task_config.get_loop_override.return_value = None

        sleep_time, use_auto, ar_secs = task._compute_sleep_time(loop_mgr)

        assert sleep_time == 300
        assert use_auto is False
        assert ar_secs is None

    def test_first_run_always_uses_3s(self, task_config, mock_display):
        task = self._make_task(task_config, mock_display)
        task.first_run = True
        loop_mgr = task_config.get_loop_manager()
        loop_mgr.rotation_interval_seconds = 300
        task_config.get_loop_override.return_value = None

        sleep_time, _, _ = task._compute_sleep_time(loop_mgr)
        assert sleep_time == 3

    def test_blanked_display_extends_to_300s(self, task_config, mock_display):
        mock_display._display_blanked = True
        task = self._make_task(task_config, mock_display)
        task.first_run = False
        loop_mgr = task_config.get_loop_manager()
        loop_mgr.rotation_interval_seconds = 60
        task_config.get_loop_override.return_value = None

        sleep_time, _, _ = task._compute_sleep_time(loop_mgr)
        assert sleep_time == 300

    def test_auto_refresh_shorter_than_loop_sets_flag(self, task_config, mock_display):
        task = self._make_task(task_config, mock_display)
        task.first_run = False
        task.auto_refresh_plugin_settings = {"autoRefresh": "1"}  # 1 minute = 60s
        task.last_display_time = datetime.now(timezone.utc)
        loop_mgr = task_config.get_loop_manager()
        loop_mgr.rotation_interval_seconds = 300
        task_config.get_loop_override.return_value = None

        sleep_time, use_auto, ar_secs = task._compute_sleep_time(loop_mgr)
        assert use_auto is True
        assert ar_secs == 60
        assert sleep_time == 60  # shorter than loop interval


# ---------------------------------------------------------------------------
# _determine_refresh_action unit tests
# ---------------------------------------------------------------------------

class TestDetermineRefreshAction:
    def _make_task(self, task_config, mock_display):
        task = RefreshTask(task_config, mock_display)
        task._set_global_status = MagicMock()
        task._stop_splash_if_needed = MagicMock()
        return task

    def _base_config_side(self, key=None, default=None):
        vals = {
            "loop_enabled": True,
            "log_system_stats": False,
            "orientation": "horizontal",
            "timezone": "UTC",
            "display_type": "mock",
            "auto_refresh_tracking": {},
        }
        if key is None:
            return dict(vals)
        return vals.get(key, default)

    def test_manual_update_takes_priority(self, task_config, mock_display):
        task = self._make_task(task_config, mock_display)
        task_config.get_config.side_effect = self._base_config_side

        action = ManualRefresh("clock", {})
        task.manual_update_request = action

        loop_mgr = task_config.get_loop_manager()
        loop_mgr.rotation_interval_seconds = 300
        latest_refresh = task_config.get_refresh_info()
        current_dt = datetime.now(timezone.utc)

        result = task._determine_refresh_action(current_dt, latest_refresh, loop_mgr, False, None)

        assert result is action
        assert task.manual_update_request == ()

    def test_loop_rotation_due_on_first_run(self, task_config, mock_display):
        """With no last rotation time, loop rotation should be due on the first call."""
        task = self._make_task(task_config, mock_display)
        task_config.get_config.side_effect = self._base_config_side
        task.manual_update_request = ()
        task.last_loop_rotation_time = None

        loop_mgr = task_config.get_loop_manager()
        loop_mgr.rotation_interval_seconds = 300
        task_config.get_loop_override.return_value = None

        # Wire loop manager to return a plugin ref
        plugin_ref = MagicMock()
        plugin_ref.plugin_id = "clock"
        loop = MagicMock()
        loop_mgr.determine_active_loop.return_value = loop
        with patch.object(task, '_determine_next_plugin_loop_mode', return_value=(loop, plugin_ref)):
            current_dt = datetime.now(timezone.utc)
            latest_refresh = task_config.get_refresh_info()
            result = task._determine_refresh_action(current_dt, latest_refresh, loop_mgr, False, None)

        assert isinstance(result, LoopRefresh)

    def test_returns_none_when_loop_disabled_and_already_displayed(self, task_config, mock_display):
        task = self._make_task(task_config, mock_display)
        task_config.get_loop_override.return_value = None
        task.manual_update_request = ()
        task._displayed_this_boot = True

        def cfg_no_loop(key=None, default=None):
            vals = {"loop_enabled": False, "log_system_stats": False}
            if key is None:
                return dict(vals)
            return vals.get(key, default)

        task_config.get_config.side_effect = cfg_no_loop

        loop_mgr = task_config.get_loop_manager()
        loop_mgr.rotation_interval_seconds = 300
        latest_refresh = task_config.get_refresh_info()
        current_dt = datetime.now(timezone.utc)

        result = task._determine_refresh_action(current_dt, latest_refresh, loop_mgr, False, None)
        assert result is None

    def test_manual_clears_request_from_queue(self, task_config, mock_display):
        task = self._make_task(task_config, mock_display)
        task_config.get_config.side_effect = self._base_config_side

        action = ManualRefresh("weather", {"units": "imperial"})
        task.manual_update_request = action

        loop_mgr = task_config.get_loop_manager()
        result = task._determine_refresh_action(
            datetime.now(timezone.utc), task_config.get_refresh_info(), loop_mgr, False, None
        )

        assert result is action
        assert task.manual_update_request == ()  # cleared


# ---------------------------------------------------------------------------
# _execute_refresh_action unit tests
# ---------------------------------------------------------------------------

class TestExecuteRefreshAction:
    def _make_task(self, task_config, mock_display):
        task = RefreshTask(task_config, mock_display)
        task._set_global_status = MagicMock()
        task._stop_splash_if_needed = MagicMock()
        return task

    def test_plugin_not_found_sets_error_status(self, task_config, mock_display):
        task = self._make_task(task_config, mock_display)
        task_config.get_plugin.return_value = None

        loop_mgr = task_config.get_loop_manager()
        action = ManualRefresh("missing_plugin", {})
        task._execute_refresh_action(action, datetime.now(timezone.utc),
                                     task_config.get_refresh_info(), loop_mgr)

        mock_display.display_image.assert_not_called()
        task._set_global_status.assert_called_with("error", "Plugin not found: missing_plugin")

    def test_plugin_returns_none_skips_display(self, task_config, mock_display):
        task = self._make_task(task_config, mock_display)

        plugin_cfg = {"id": "clock", "display_name": "Clock", "class": "Clock", "image_settings": []}
        task_config.get_plugin.return_value = plugin_cfg

        mock_plugin = MagicMock()
        mock_plugin.config = {"image_settings": []}
        mock_plugin.generate_image.return_value = None  # simulate grace-period skip

        loop_mgr = task_config.get_loop_manager()
        action = ManualRefresh("clock", {})

        with patch("refresh_task.get_plugin_instance", return_value=mock_plugin):
            task._execute_refresh_action(action, datetime.now(timezone.utc),
                                         task_config.get_refresh_info(), loop_mgr)

        mock_display.display_image.assert_not_called()

    def test_successful_render_calls_display_image(self, task_config, mock_display):
        from plugins.plugin_registry import load_plugins, PLUGIN_CLASSES

        plugin_cfg = {"id": "clock", "display_name": "Clock", "class": "Clock", "image_settings": []}
        if "clock" not in PLUGIN_CLASSES:
            load_plugins([plugin_cfg])
        task_config.get_plugin.side_effect = lambda pid: plugin_cfg if pid == "clock" else None

        task = self._make_task(task_config, mock_display)
        loop_mgr = task_config.get_loop_manager()
        action = ManualRefresh("clock", {"face": "digital", "showTitle": "false"})

        task._execute_refresh_action(action, datetime.now(timezone.utc),
                                     task_config.get_refresh_info(), loop_mgr)

        mock_display.display_image.assert_called_once()
        img_arg = mock_display.display_image.call_args[0][0]
        from PIL import Image as PILImage
        assert isinstance(img_arg, PILImage.Image)
