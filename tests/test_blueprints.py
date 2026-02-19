"""Tests for Flask blueprint routes across all 5 blueprints.

Uses the `client` fixture from conftest.py which provides a Flask test client
with mock DEVICE_CONFIG, REFRESH_TASK, and DISPLAY_MANAGER injected.
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock
from PIL import Image


# ===========================================================================
# Main Blueprint
# ===========================================================================

class TestMainBlueprint:
    def test_main_page(self, client, flask_app):
        # Need templates to exist — if they don't, we'll get a 500
        # Since templates may reference variables, we test that the route is registered
        with flask_app.test_request_context():
            rules = [r.rule for r in flask_app.url_map.iter_rules()]
            assert "/" in rules

    def test_display_page_registered(self, flask_app):
        with flask_app.test_request_context():
            rules = [r.rule for r in flask_app.url_map.iter_rules()]
            assert "/display" in rules

    def test_current_image_200(self, client, flask_app, tmp_path):
        # Create an image at the path the blueprint expects
        img_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "src", "static", "images", "current_image.png")
        if os.path.exists(img_path):
            resp = client.get("/api/current_image")
            assert resp.status_code == 200
            assert resp.content_type == "image/png"
        else:
            # Create it for the test
            os.makedirs(os.path.dirname(img_path), exist_ok=True)
            Image.new("RGB", (800, 480), "white").save(img_path)
            try:
                resp = client.get("/api/current_image")
                assert resp.status_code == 200
            finally:
                os.remove(img_path)

    def test_current_image_404_when_missing(self, client, flask_app):
        with patch("blueprints.main.os.path.exists", return_value=False):
            resp = client.get("/api/current_image")
            assert resp.status_code == 404

    def test_current_image_304_not_modified(self, client, flask_app):
        # Ensure the image exists at the path the blueprint expects
        img_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "src", "static", "images", "current_image.png")
        created = False
        if not os.path.exists(img_path):
            os.makedirs(os.path.dirname(img_path), exist_ok=True)
            Image.new("RGB", (800, 480), "white").save(img_path)
            created = True
        try:
            resp1 = client.get("/api/current_image")
            assert resp1.status_code == 200
            last_modified = resp1.headers.get("Last-Modified")
            assert last_modified is not None

            resp2 = client.get("/api/current_image", headers={
                "If-Modified-Since": last_modified
            })
            assert resp2.status_code == 304
        finally:
            if created:
                os.remove(img_path)

    def test_plugin_order_valid(self, client):
        resp = client.post("/api/plugin_order",
                          data=json.dumps({"order": ["clock", "weather"]}),
                          content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_plugin_order_invalid(self, client):
        resp = client.post("/api/plugin_order",
                          data=json.dumps({"order": "not-a-list"}),
                          content_type="application/json")
        assert resp.status_code == 400

    def test_toggle_loop_enable(self, client):
        resp = client.post("/toggle_loop",
                          data=json.dumps({"enabled": True}),
                          content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enabled"] is True

    def test_toggle_loop_disable(self, client):
        resp = client.post("/toggle_loop",
                          data=json.dumps({"enabled": False}),
                          content_type="application/json")
        assert resp.status_code == 200

    def test_toggle_loop_missing_field(self, client):
        resp = client.post("/toggle_loop",
                          data=json.dumps({}),
                          content_type="application/json")
        assert resp.status_code == 400

    def test_weather_location_empty(self, client):
        resp = client.get("/api/weather_location")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["latitude"] is None


# ===========================================================================
# Settings Blueprint
# ===========================================================================

class TestSettingsBlueprint:
    def test_settings_page_registered(self, flask_app):
        with flask_app.test_request_context():
            rules = [r.rule for r in flask_app.url_map.iter_rules()]
            assert "/settings" in rules

    def test_save_settings_valid(self, client):
        resp = client.post("/save_settings", data={
            "deviceName": "TestPi",
            "orientation": "horizontal",
            "timezoneName": "US/Central",
            "timeFormat": "12h",
            "saturation": "1.0",
            "brightness": "1.0",
            "sharpness": "1.0",
            "contrast": "1.0",
        })
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_save_settings_missing_timezone(self, client):
        resp = client.post("/save_settings", data={
            "timeFormat": "12h",
        })
        assert resp.status_code == 400

    def test_save_settings_invalid_time_format(self, client):
        resp = client.post("/save_settings", data={
            "timezoneName": "US/Central",
            "timeFormat": "invalid",
        })
        assert resp.status_code == 400


# ===========================================================================
# Plugin Blueprint
# ===========================================================================

class TestPluginBlueprint:
    def test_plugin_not_found(self, client):
        resp = client.get("/plugin/nonexistent_plugin_xyz")
        assert resp.status_code == 404

    def test_stocks_get_settings(self, client):
        resp = client.get("/plugin/stocks/settings")
        assert resp.status_code == 200

    def test_stocks_save_settings(self, client):
        resp = client.post("/plugin/stocks/settings",
                          data=json.dumps({"settings": {"autoRefresh": True}}),
                          content_type="application/json")
        assert resp.status_code == 200

    def test_stocks_save_settings_missing(self, client):
        resp = client.post("/plugin/stocks/settings",
                          data=json.dumps({}),
                          content_type="application/json")
        assert resp.status_code == 400

    def test_stocks_get_tickers(self, client):
        resp = client.get("/plugin/stocks/tickers")
        assert resp.status_code == 200

    def test_stocks_save_tickers_valid(self, client, flask_app):
        # Set up existing tickers
        cfg = flask_app.config["DEVICE_CONFIG"]
        cfg.get_config.side_effect = None
        cfg.get_config.return_value = [
            {"symbol": "AAPL", "name": "Apple"},
            {"symbol": "MSFT", "name": "Microsoft"},
        ]
        resp = client.post("/plugin/stocks/tickers",
                          data=json.dumps({"tickers": ["AAPL", "MSFT"]}),
                          content_type="application/json")
        assert resp.status_code == 200

    def test_stocks_save_tickers_invalid(self, client):
        resp = client.post("/plugin/stocks/tickers",
                          data=json.dumps({"tickers": "not-a-list"}),
                          content_type="application/json")
        assert resp.status_code == 400

    def test_stocks_remove_ticker_not_found(self, client):
        resp = client.delete("/plugin/stocks/tickers/FAKE")
        assert resp.status_code == 404

    def test_stocks_add_ticker_missing(self, client):
        resp = client.post("/plugin/stocks/tickers/add",
                          data=json.dumps({}),
                          content_type="application/json")
        assert resp.status_code == 400

    def test_update_now_async(self, client):
        resp = client.post("/update_now_async", data={
            "plugin_id": "clock",
        })
        assert resp.status_code == 202


# ===========================================================================
# Loops Blueprint
# ===========================================================================

class TestLoopsBlueprint:
    def test_loops_page_registered(self, flask_app):
        with flask_app.test_request_context():
            rules = [r.rule for r in flask_app.url_map.iter_rules()]
            assert "/loops" in rules

    def test_create_loop(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        lm.add_loop.return_value = True
        resp = client.post("/create_loop",
                          data=json.dumps({
                              "name": "Test Loop",
                              "start_time": "09:00",
                              "end_time": "17:00",
                          }),
                          content_type="application/json")
        assert resp.status_code == 200

    def test_create_loop_missing_fields(self, client):
        resp = client.post("/create_loop",
                          data=json.dumps({"name": "Incomplete"}),
                          content_type="application/json")
        assert resp.status_code == 400

    def test_create_loop_duplicate(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        lm.add_loop.return_value = False
        resp = client.post("/create_loop",
                          data=json.dumps({
                              "name": "Dup",
                              "start_time": "09:00",
                              "end_time": "17:00",
                          }),
                          content_type="application/json")
        assert resp.status_code == 400

    def test_update_loop(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        lm.update_loop.return_value = True
        resp = client.post("/update_loop",
                          data=json.dumps({
                              "old_name": "Old",
                              "new_name": "New",
                              "start_time": "08:00",
                              "end_time": "20:00",
                          }),
                          content_type="application/json")
        assert resp.status_code == 200

    def test_update_loop_not_found(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        lm.update_loop.return_value = False
        resp = client.post("/update_loop",
                          data=json.dumps({
                              "old_name": "Nope",
                              "new_name": "Still Nope",
                              "start_time": "08:00",
                              "end_time": "20:00",
                          }),
                          content_type="application/json")
        assert resp.status_code == 404

    def test_delete_loop(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        mock_loop = MagicMock()
        lm.get_loop.return_value = mock_loop
        resp = client.post("/delete_loop",
                          data=json.dumps({"loop_name": "Test"}),
                          content_type="application/json")
        assert resp.status_code == 200

    def test_delete_loop_not_found(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        lm.get_loop.return_value = None
        resp = client.post("/delete_loop",
                          data=json.dumps({"loop_name": "Ghost"}),
                          content_type="application/json")
        assert resp.status_code == 404

    def test_add_plugin_to_loop(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        cfg = flask_app.config["DEVICE_CONFIG"]
        mock_loop = MagicMock()
        mock_loop.add_plugin.return_value = True
        mock_loop.plugin_order = []
        lm.get_loop.return_value = mock_loop
        cfg.get_plugin.return_value = {"id": "clock", "display_name": "Clock"}
        resp = client.post("/add_plugin_to_loop",
                          data=json.dumps({
                              "loop_name": "Default",
                              "plugin_id": "clock",
                              "refresh_interval_seconds": 300,
                          }),
                          content_type="application/json")
        assert resp.status_code == 200

    def test_add_plugin_loop_not_found(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        lm.get_loop.return_value = None
        resp = client.post("/add_plugin_to_loop",
                          data=json.dumps({
                              "loop_name": "Missing",
                              "plugin_id": "clock",
                          }),
                          content_type="application/json")
        assert resp.status_code == 404

    def test_remove_plugin_from_loop(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        mock_loop = MagicMock()
        mock_loop.remove_plugin.return_value = True
        lm.get_loop.return_value = mock_loop
        resp = client.post("/remove_plugin_from_loop",
                          data=json.dumps({
                              "loop_name": "Default",
                              "plugin_id": "clock",
                          }),
                          content_type="application/json")
        assert resp.status_code == 200

    def test_remove_plugin_not_in_loop(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        mock_loop = MagicMock()
        mock_loop.remove_plugin.return_value = False
        lm.get_loop.return_value = mock_loop
        resp = client.post("/remove_plugin_from_loop",
                          data=json.dumps({
                              "loop_name": "Default",
                              "plugin_id": "ghost",
                          }),
                          content_type="application/json")
        assert resp.status_code == 404

    def test_reorder_plugins(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        mock_loop = MagicMock()
        lm.get_loop.return_value = mock_loop
        resp = client.post("/reorder_plugins",
                          data=json.dumps({
                              "loop_name": "Default",
                              "plugin_ids": ["weather", "clock"],
                          }),
                          content_type="application/json")
        assert resp.status_code == 200

    def test_reorder_plugins_loop_not_found(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        lm.get_loop.return_value = None
        resp = client.post("/reorder_plugins",
                          data=json.dumps({
                              "loop_name": "Missing",
                              "plugin_ids": [],
                          }),
                          content_type="application/json")
        assert resp.status_code == 404

    def test_update_rotation_interval(self, client, flask_app):
        resp = client.post("/update_rotation_interval",
                          data=json.dumps({"interval": 10, "unit": "minute"}),
                          content_type="application/json")
        assert resp.status_code == 200

    def test_update_rotation_interval_missing(self, client):
        resp = client.post("/update_rotation_interval",
                          data=json.dumps({}),
                          content_type="application/json")
        assert resp.status_code == 400

    def test_toggle_loop_randomize(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        mock_loop = MagicMock()
        mock_loop.randomize = False
        lm.get_loop.return_value = mock_loop
        resp = client.post("/toggle_loop_randomize",
                          data=json.dumps({"loop_name": "Default"}),
                          content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["randomize"] is True

    def test_toggle_loop_randomize_not_found(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        lm.get_loop.return_value = None
        resp = client.post("/toggle_loop_randomize",
                          data=json.dumps({"loop_name": "Missing"}),
                          content_type="application/json")
        assert resp.status_code == 404

    def test_update_plugin_settings(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        mock_loop = MagicMock()
        mock_ref = MagicMock()
        mock_ref.plugin_id = "clock"
        mock_loop.plugin_order = [mock_ref]
        lm.get_loop.return_value = mock_loop
        resp = client.post("/update_plugin_settings",
                          data=json.dumps({
                              "loop_name": "Default",
                              "plugin_id": "clock",
                              "plugin_settings": {"face": "Digital"},
                          }),
                          content_type="application/json")
        assert resp.status_code == 200

    def test_update_plugin_settings_loop_not_found(self, client, flask_app):
        lm = flask_app.config["DEVICE_CONFIG"].get_loop_manager()
        lm.get_loop.return_value = None
        resp = client.post("/update_plugin_settings",
                          data=json.dumps({
                              "loop_name": "Missing",
                              "plugin_id": "clock",
                              "plugin_settings": {},
                          }),
                          content_type="application/json")
        assert resp.status_code == 404

    @patch("blueprints.loops.get_http_session")
    def test_search_city(self, mock_session, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{
                "name": "Dallas",
                "country": "US",
                "admin1": "Texas",
                "latitude": 32.78,
                "longitude": -96.80,
            }]
        }
        mock_session.return_value.get.return_value = mock_resp
        resp = client.post("/search_city",
                          data=json.dumps({"city_name": "Dallas"}),
                          content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["cities"]) == 1

    def test_search_city_empty(self, client):
        resp = client.post("/search_city",
                          data=json.dumps({"city_name": ""}),
                          content_type="application/json")
        assert resp.status_code == 400


# ===========================================================================
# API Keys Blueprint
# ===========================================================================

class TestApiKeysBlueprint:
    def test_apikeys_page_registered(self, flask_app):
        with flask_app.test_request_context():
            rules = [r.rule for r in flask_app.url_map.iter_rules()]
            assert "/api-keys" in rules

    @patch("blueprints.apikeys.get_env_path")
    def test_save_apikeys_valid(self, mock_env_path, client, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        mock_env_path.return_value = str(env_file)

        resp = client.post("/api-keys/save",
                          data=json.dumps({
                              "entries": [
                                  {"key": "OPENAI_API_KEY", "value": "sk-test123"},
                                  {"key": "NASA_API_KEY", "value": "DEMO_KEY"},
                              ]
                          }),
                          content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch("blueprints.apikeys.get_env_path")
    def test_save_apikeys_invalid_key_format(self, mock_env_path, client, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        mock_env_path.return_value = str(env_file)

        resp = client.post("/api-keys/save",
                          data=json.dumps({
                              "entries": [
                                  {"key": "123-INVALID", "value": "test"},
                              ]
                          }),
                          content_type="application/json")
        assert resp.status_code == 400

    @patch("blueprints.apikeys.get_env_path")
    def test_save_apikeys_key_too_long(self, mock_env_path, client, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        mock_env_path.return_value = str(env_file)

        resp = client.post("/api-keys/save",
                          data=json.dumps({
                              "entries": [
                                  {"key": "A" * 101, "value": "test"},
                              ]
                          }),
                          content_type="application/json")
        assert resp.status_code == 400

    @patch("blueprints.apikeys.get_env_path")
    def test_save_apikeys_keep_existing(self, mock_env_path, client, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING_KEY=secret_value\n")
        mock_env_path.return_value = str(env_file)

        resp = client.post("/api-keys/save",
                          data=json.dumps({
                              "entries": [
                                  {"key": "EXISTING_KEY", "keepExisting": True},
                              ]
                          }),
                          content_type="application/json")
        assert resp.status_code == 200

    @patch("blueprints.apikeys.get_env_path")
    def test_save_apikeys_empty(self, mock_env_path, client, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        mock_env_path.return_value = str(env_file)

        resp = client.post("/api-keys/save",
                          data=json.dumps({"entries": []}),
                          content_type="application/json")
        assert resp.status_code == 200
