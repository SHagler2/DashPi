"""Shared fixtures for DashPi test suite."""

import os
import json
import pytest
from unittest.mock import MagicMock, patch
from PIL import Image
from flask import Flask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def assert_valid_image(img, expected_size=None):
    """Assert that img is a valid PIL Image, optionally checking dimensions."""
    assert isinstance(img, Image.Image), f"Expected PIL Image, got {type(img)}"
    assert img.size[0] > 0 and img.size[1] > 0, "Image has zero dimensions"
    if expected_size:
        assert img.size == expected_size, f"Expected {expected_size}, got {img.size}"


# ---------------------------------------------------------------------------
# Mock device config
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_device_config():
    """MagicMock that behaves like Config for plugin / blueprint tests."""
    cfg = MagicMock()
    cfg.get_resolution.return_value = (800, 480)
    cfg.get_config.side_effect = _config_side_effect
    cfg.load_env_key.return_value = None
    cfg.get_plugins.return_value = []
    cfg.get_plugin.return_value = None
    cfg.current_image_file = "/tmp/dashpi_test_current_image.png"
    cfg.plugin_image_dir = "/tmp/dashpi_test_plugin_images"

    # Loop manager mock
    loop_mgr = MagicMock()
    loop_mgr.loops = []
    loop_mgr.rotation_interval_seconds = 300
    loop_mgr.get_loop_names.return_value = []
    loop_mgr.get_loop.return_value = None
    loop_mgr.to_dict.return_value = {"loops": [], "rotation_interval_seconds": 300, "active_loop": None}
    loop_mgr.determine_active_loop.return_value = None
    cfg.get_loop_manager.return_value = loop_mgr

    # Refresh info mock
    refresh_info = MagicMock()
    refresh_info.to_dict.return_value = {
        "refresh_time": None, "image_hash": None,
        "refresh_type": None, "plugin_id": None
    }
    refresh_info.get_refresh_datetime.return_value = None
    refresh_info.plugin_id = None
    cfg.get_refresh_info.return_value = refresh_info

    return cfg


def _config_side_effect(key=None, default=None):
    """Return sensible defaults for common config keys."""
    defaults = {
        "orientation": "horizontal",
        "timezone": "US/Central",
        "time_format": "12h",
        "resolution": [800, 480],
        "name": "TestPi",
        "display_type": "mock",
        "loop_enabled": True,
        "image_settings": {},
    }
    if key is None:
        return dict(defaults)
    if default is not None:
        return defaults.get(key, default)
    return defaults.get(key, {})


# ---------------------------------------------------------------------------
# Flask test app & client
# ---------------------------------------------------------------------------

@pytest.fixture
def flask_app(mock_device_config, tmp_path):
    """Bare Flask app with all blueprints registered and mock deps injected."""
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "..", "src", "templates"),
    )
    app.config["TESTING"] = True
    app.secret_key = "test-secret"

    # Inject mock dependencies
    app.config["DEVICE_CONFIG"] = mock_device_config

    refresh_task = MagicMock()
    refresh_task.running = True
    refresh_task.queue_manual_update.return_value = True
    refresh_task.signal_config_change.return_value = None
    app.config["REFRESH_TASK"] = refresh_task

    display_manager = MagicMock()
    app.config["DISPLAY_MANAGER"] = display_manager

    # Create a current_image file for endpoints that serve it
    current_img_path = str(tmp_path / "current_image.png")
    Image.new("RGB", (800, 480), "white").save(current_img_path)
    mock_device_config.current_image_file = current_img_path

    # Register blueprints — import here to avoid import-time side effects
    from blueprints.main import main_bp
    from blueprints.settings import settings_bp
    from blueprints.plugin import plugin_bp
    from blueprints.apikeys import apikeys_bp
    from blueprints.loops import loops_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(plugin_bp)
    app.register_blueprint(apikeys_bp)
    app.register_blueprint(loops_bp)

    return app


@pytest.fixture
def client(flask_app):
    """Flask test client."""
    return flask_app.test_client()
