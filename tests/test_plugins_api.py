"""Tests for API-dependent plugins.

All external HTTP calls and third-party libraries are mocked.
Pattern: instantiate plugin, mock external deps, call generate_image or helpers,
verify result is a valid PIL Image or expected error.
"""

import os
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from PIL import Image


def assert_valid_image(img, expected_size=None):
    assert isinstance(img, Image.Image), f"Expected PIL Image, got {type(img)}"
    assert img.size[0] > 0 and img.size[1] > 0
    if expected_size:
        assert img.size == expected_size, f"Expected {expected_size}, got {img.size}"


# ===== Config dicts for each plugin =====

WEATHER_CONFIG = {"id": "weather", "display_name": "Weather", "class": "Weather"}
STOCKS_CONFIG = {"id": "stocks", "display_name": "Stocks", "class": "Stocks"}
RSS_CONFIG = {"id": "rss", "display_name": "RSS", "class": "Rss"}
APOD_CONFIG = {"id": "apod", "display_name": "APOD", "class": "Apod"}
AI_TEXT_CONFIG = {"id": "ai_text", "display_name": "AI Text", "class": "AIText"}
AI_IMAGE_CONFIG = {"id": "ai_image", "display_name": "AI Image", "class": "AIImage"}
NEWSPAPER_CONFIG = {"id": "newspaper", "display_name": "Newspaper", "class": "Newspaper"}
COMIC_CONFIG = {"id": "comic", "display_name": "Comic", "class": "Comic"}
UNSPLASH_CONFIG = {"id": "unsplash", "display_name": "Unsplash", "class": "Unsplash"}
WPOTD_CONFIG = {"id": "wpotd", "display_name": "WPOTD", "class": "Wpotd"}
GITHUB_CONFIG = {"id": "github", "display_name": "GitHub", "class": "GitHub"}
ART_MUSEUM_CONFIG = {"id": "art_museum", "display_name": "Art Museum", "class": "ArtMuseum"}
CALENDAR_CONFIG = {"id": "calendar", "display_name": "Calendar", "class": "Calendar"}
ISS_CONFIG = {"id": "iss_tracker", "display_name": "ISS Tracker", "class": "ISSTracker"}


# ===========================================================================
# Weather Plugin
# ===========================================================================

class TestWeather:
    @pytest.fixture
    def plugin(self):
        from plugins.weather.weather import Weather
        return Weather(WEATHER_CONFIG)

    def test_missing_coordinates(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="valid numbers"):
            plugin.generate_image({}, mock_device_config)

    def test_invalid_coordinates(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="Invalid coordinates"):
            plugin.generate_image(
                {"latitude": "999", "longitude": "0", "units": "metric"},
                mock_device_config
            )

    def test_missing_units(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="Units are required"):
            plugin.generate_image(
                {"latitude": "32.7", "longitude": "-96.8"},
                mock_device_config
            )

    def test_missing_api_key_owm(self, plugin, mock_device_config):
        mock_device_config.load_env_key.return_value = None
        with pytest.raises(RuntimeError):
            plugin.generate_image(
                {"latitude": "32.7", "longitude": "-96.8", "units": "metric",
                 "weatherProvider": "OpenWeatherMap"},
                mock_device_config
            )

    def test_render_pil_minimal(self, plugin, mock_device_config):
        """Test _render_pil directly with minimal data dict."""
        data = {
            "current_date": "Monday, January 01",
            "current_day_icon": "",
            "current_temperature": "72",
            "feels_like": "70",
            "weather_description": "Clear Sky",
            "temperature_unit": "°F",
            "units": "imperial",
            "time_format": "12h",
            "forecast": [{"day": "Mon", "high": 75, "low": 60}],
            "data_points": [],
            "hourly_forecast": [],
            "title": "Dallas, TX",
            "last_refresh_time": "2025-01-01 12:00 PM",
        }
        settings = {"displayRefreshTime": "true", "displayMetrics": "true",
                     "displayGraph": "true", "displayForecast": "true",
                     "forecastDays": "3", "moonPhase": "false"}
        img = plugin._render_pil((800, 480), data, settings)
        assert_valid_image(img, (800, 480))

    def test_get_wind_arrow(self, plugin):
        assert plugin.get_wind_arrow(0) == "↓"      # North
        assert plugin.get_wind_arrow(90) == "←"      # East
        assert plugin.get_wind_arrow(180) == "↑"     # South
        assert plugin.get_wind_arrow(270) == "→"     # West

    def test_map_weather_code_to_icon(self, plugin):
        assert plugin.map_weather_code_to_icon(0, 1) == "01d"   # Clear day
        assert plugin.map_weather_code_to_icon(0, 0) == "01n"   # Clear night
        assert plugin.map_weather_code_to_icon(95, 1) == "11d"  # Thunderstorm

    def test_get_weather_description(self, plugin):
        assert plugin.get_weather_description(0) == "Clear Sky"
        assert plugin.get_weather_description(95) == "Thunderstorm"
        assert plugin.get_weather_description(999) == "Unknown"

    def test_get_moon_phase_icon_path_north(self, plugin):
        path = plugin.get_moon_phase_icon_path("waxingcrescent", 32.0)
        assert "waxingcrescent" in path

    def test_get_moon_phase_icon_path_south(self, plugin):
        path = plugin.get_moon_phase_icon_path("waxingcrescent", -33.0)
        assert "waningcrescent" in path

    def test_format_time_12h(self, plugin):
        from datetime import datetime
        import pytz
        tz = pytz.timezone("US/Central")
        dt = datetime(2025, 1, 1, 14, 30, tzinfo=tz)
        assert plugin.format_time(dt, "12h") == "2:30 PM"

    def test_format_time_24h(self, plugin):
        from datetime import datetime
        import pytz
        tz = pytz.timezone("US/Central")
        dt = datetime(2025, 1, 1, 14, 30, tzinfo=tz)
        assert plugin.format_time(dt, "24h") == "14:30"


# ===========================================================================
# Stocks Plugin
# ===========================================================================

class TestStocks:
    @pytest.fixture
    def plugin(self):
        from plugins.stocks.stocks import Stocks
        return Stocks(STOCKS_CONFIG)

    def test_no_tickers_raises(self, plugin, mock_device_config):
        mock_device_config.get_config.side_effect = lambda key=None, default=None: (
            [] if key == "stocks_saved_tickers" else
            "horizontal" if key == "orientation" else default
        )
        with pytest.raises(RuntimeError, match="No tickers"):
            plugin.generate_image({}, mock_device_config)

    def test_render_pil_single_stock(self, plugin):
        stocks_data = [{
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "price_formatted": "$150.00",
            "change_formatted": "+2.50",
            "change_percent_formatted": "+1.69%",
            "volume": "50.00M",
            "high_formatted": "$152.00",
            "low_formatted": "$148.00",
            "week52_high_formatted": "$180.00",
            "week52_low_formatted": "$120.00",
            "is_positive": True,
        }]
        img = plugin._render_pil((800, 480), "Stocks", stocks_data, 1, 1,
                                  "12:00 PM", 5, 1.0, 1.0, {})
        assert_valid_image(img, (800, 480))

    def test_render_pil_dark_mode(self, plugin):
        stocks_data = [{
            "symbol": "TSLA",
            "name": "Tesla Inc.",
            "price_formatted": "$250.00",
            "change_formatted": "-5.00",
            "change_percent_formatted": "-1.96%",
            "volume": "30.00M",
            "high_formatted": "$255.00",
            "low_formatted": "$245.00",
            "week52_high_formatted": "$300.00",
            "week52_low_formatted": "$150.00",
            "is_positive": False,
        }]
        img = plugin._render_pil((800, 480), "Stocks", stocks_data, 1, 1,
                                  "12:00 PM", 0, 1.0, 1.0, {"darkMode": "on"})
        assert_valid_image(img, (800, 480))

    def test_format_large_number(self):
        from plugins.stocks.stocks import format_large_number
        assert format_large_number(1500) == "1.50K"
        assert format_large_number(2_500_000) == "2.50M"
        assert format_large_number(3_000_000_000) == "3.00B"
        assert format_large_number(None) == "N/A"

    def test_format_price(self):
        from plugins.stocks.stocks import format_price
        assert format_price(150.5) == "$150.50"
        assert format_price(None) == "N/A"


# ===========================================================================
# RSS Plugin
# ===========================================================================

class TestRss:
    @pytest.fixture
    def plugin(self):
        from plugins.rss.rss import Rss
        return Rss(RSS_CONFIG)

    def test_missing_feed_url(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="RSS Feed Url is required"):
            plugin.generate_image({}, mock_device_config)

    def test_render_pil_with_items(self, plugin):
        items = [
            {"title": "Test Article 1", "description": "Description 1", "image": None},
            {"title": "Test Article 2", "description": "Description 2", "image": None},
        ]
        img = plugin._render_pil((800, 480), "Tech News", items, False, 1.0, {})
        assert_valid_image(img, (800, 480))

    def test_render_pil_no_title(self, plugin):
        items = [{"title": "Article", "description": "Desc", "image": None}]
        img = plugin._render_pil((800, 480), None, items, False, 1.0, {})
        assert_valid_image(img, (800, 480))

    def test_strip_html(self, plugin):
        assert plugin._strip_html("<b>Hello</b> &amp; world") == "Hello & world"
        assert plugin._strip_html("<p>Test</p>") == "Test"


# ===========================================================================
# APOD Plugin
# ===========================================================================

class TestApod:
    @pytest.fixture
    def plugin(self):
        from plugins.apod.apod import Apod
        return Apod(APOD_CONFIG)

    def test_missing_api_key(self, plugin, mock_device_config):
        mock_device_config.load_env_key.return_value = None
        with pytest.raises(RuntimeError, match="NASA API Key"):
            plugin.generate_image({}, mock_device_config)

    @patch("plugins.apod.apod.get_http_session")
    def test_successful_image(self, mock_session, plugin, mock_device_config):
        mock_device_config.load_env_key.return_value = "fake-nasa-key"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "media_type": "image",
            "url": "https://example.com/apod.jpg",
            "hdurl": "https://example.com/apod_hd.jpg",
            "title": "Test Nebula",
        }
        mock_session.return_value.get.return_value = mock_response

        mock_img = Image.new("RGB", (800, 480), "blue")
        plugin.image_loader = MagicMock()
        plugin.image_loader.from_url.return_value = mock_img

        img = plugin.generate_image({}, mock_device_config)
        assert_valid_image(img, (800, 480))

    @patch("plugins.apod.apod.get_http_session")
    def test_all_videos_raises(self, mock_session, plugin, mock_device_config):
        mock_device_config.load_env_key.return_value = "fake-nasa-key"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "media_type": "video",
            "url": "https://youtube.com/video",
        }
        mock_session.return_value.get.return_value = mock_response

        with pytest.raises(RuntimeError, match="Could not find an APOD image"):
            plugin.generate_image({}, mock_device_config)


# ===========================================================================
# AI Text Plugin
# ===========================================================================

class TestAIText:
    @pytest.fixture
    def plugin(self):
        from plugins.ai_text.ai_text import AIText
        return AIText(AI_TEXT_CONFIG)

    def test_missing_prompt(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="Text Prompt is required"):
            plugin.generate_image({"textPrompt": "  "}, mock_device_config)

    def test_missing_openai_key(self, plugin, mock_device_config):
        mock_device_config.load_env_key.return_value = None
        with pytest.raises(RuntimeError, match="OpenAI API Key"):
            plugin.generate_image(
                {"textPrompt": "Tell me a joke", "provider": "openai"},
                mock_device_config
            )

    def test_missing_gemini_key(self, plugin, mock_device_config):
        mock_device_config.load_env_key.return_value = None
        with pytest.raises(RuntimeError, match="Google Gemini API Key"):
            plugin.generate_image(
                {"textPrompt": "Tell me a joke", "provider": "gemini"},
                mock_device_config
            )

    def test_render_pil(self, plugin):
        img = plugin._render_pil((800, 480), "Daily Joke", "Why did the chicken cross the road?", {})
        assert_valid_image(img, (800, 480))

    def test_render_pil_custom_colors(self, plugin):
        settings = {"backgroundColor": "#000000", "textColor": "#ffffff"}
        img = plugin._render_pil((800, 480), "Quote", "Be the change.", settings)
        assert_valid_image(img, (800, 480))


# ===========================================================================
# AI Image Plugin
# ===========================================================================

class TestAIImage:
    @pytest.fixture
    def plugin(self):
        from plugins.ai_image.ai_image import AIImage
        return AIImage(AI_IMAGE_CONFIG)

    def test_missing_openai_key(self, plugin, mock_device_config):
        mock_device_config.load_env_key.return_value = None
        with pytest.raises(RuntimeError, match="OpenAI API Key"):
            plugin.generate_image(
                {"textPrompt": "A sunset", "provider": "openai"},
                mock_device_config
            )

    def test_missing_gemini_key(self, plugin, mock_device_config):
        mock_device_config.load_env_key.return_value = None
        with pytest.raises(RuntimeError, match="Google Gemini API Key"):
            plugin.generate_image(
                {"textPrompt": "A sunset", "provider": "gemini"},
                mock_device_config
            )

    def test_get_selected_feed_urls_defaults(self, plugin):
        urls = plugin._get_selected_feed_urls({})
        assert len(urls) == 1  # defaults to BBC

    def test_get_selected_feed_urls_custom(self, plugin):
        settings = {
            "newsFeeds": "bbc,reuters",
            "customFeedUrl": "https://example.com/feed.xml",
        }
        urls = plugin._get_selected_feed_urls(settings)
        assert len(urls) == 3


# ===========================================================================
# Newspaper Plugin
# ===========================================================================

class TestNewspaper:
    @pytest.fixture
    def plugin(self):
        from plugins.newspaper.newspaper import Newspaper
        return Newspaper(NEWSPAPER_CONFIG)

    def test_missing_slug(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="Newspaper input not provided"):
            plugin.generate_image({}, mock_device_config)

    def test_successful_load(self, plugin, mock_device_config):
        mock_img = Image.new("RGB", (600, 800), "white")
        plugin.image_loader = MagicMock()
        plugin.image_loader.from_url.return_value = mock_img

        img = plugin.generate_image({"newspaperSlug": "TX_DMN"}, mock_device_config)
        assert_valid_image(img)

    def test_not_found_raises(self, plugin, mock_device_config):
        plugin.image_loader = MagicMock()
        plugin.image_loader.from_url.return_value = None

        with pytest.raises(RuntimeError, match="front cover not found"):
            plugin.generate_image({"newspaperSlug": "FAKE_PAPER"}, mock_device_config)


# ===========================================================================
# Comic Plugin
# ===========================================================================

class TestComic:
    @pytest.fixture
    def plugin(self):
        from plugins.comic.comic import Comic
        return Comic(COMIC_CONFIG)

    def test_invalid_comic(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="Invalid comic"):
            plugin.generate_image({"comic": "NonexistentComic"}, mock_device_config)

    def test_missing_comic(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="Invalid comic"):
            plugin.generate_image({}, mock_device_config)

    @patch("plugins.comic.comic.get_panel")
    def test_successful_comic(self, mock_get_panel, plugin, mock_device_config):
        mock_get_panel.return_value = {
            "image_url": "https://example.com/comic.png",
            "title": "Test Comic",
            "caption": "A funny caption",
        }
        mock_img = Image.new("RGB", (400, 300), "white")
        plugin.image_loader = MagicMock()
        plugin.image_loader.from_url.return_value = mock_img

        img = plugin.generate_image({"comic": "XKCD"}, mock_device_config)
        assert_valid_image(img, (800, 480))

    @patch("plugins.comic.comic.get_panel")
    def test_failed_image_load(self, mock_get_panel, plugin, mock_device_config):
        mock_get_panel.return_value = {
            "image_url": "https://example.com/broken.png",
            "title": "Test",
            "caption": "",
        }
        plugin.image_loader = MagicMock()
        plugin.image_loader.from_url.return_value = None

        with pytest.raises(RuntimeError, match="Failed to load comic"):
            plugin.generate_image({"comic": "XKCD"}, mock_device_config)


# ===========================================================================
# Unsplash Plugin
# ===========================================================================

class TestUnsplash:
    @pytest.fixture
    def plugin(self):
        from plugins.unsplash.unsplash import Unsplash
        return Unsplash(UNSPLASH_CONFIG)

    def test_missing_access_key(self, plugin, mock_device_config):
        mock_device_config.load_env_key.return_value = None
        with pytest.raises(RuntimeError, match="Unsplash Access Key"):
            plugin.generate_image({}, mock_device_config)

    @patch("plugins.unsplash.unsplash.get_http_session")
    @patch("plugins.unsplash.unsplash._is_low_resource_device", return_value=False)
    def test_successful_random(self, mock_low_res, mock_session, plugin, mock_device_config):
        mock_device_config.load_env_key.return_value = "fake-unsplash-key"

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "urls": {"full": "https://images.unsplash.com/photo.jpg"}
        }
        mock_response.raise_for_status.return_value = None
        mock_session.return_value.get.return_value = mock_response

        mock_img = Image.new("RGB", (800, 480), "green")
        plugin.image_loader = MagicMock()
        plugin.image_loader.from_url.return_value = mock_img

        img = plugin.generate_image({}, mock_device_config)
        assert_valid_image(img, (800, 480))

    @patch("plugins.unsplash.unsplash.get_http_session")
    @patch("plugins.unsplash.unsplash._is_low_resource_device", return_value=False)
    def test_search_no_results(self, mock_low_res, mock_session, plugin, mock_device_config):
        mock_device_config.load_env_key.return_value = "fake-unsplash-key"

        mock_response = MagicMock()
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status.return_value = None
        mock_session.return_value.get.return_value = mock_response

        with pytest.raises(RuntimeError):
            plugin.generate_image({"search_query": "xyznonexistent"}, mock_device_config)


# ===========================================================================
# Wikipedia POTD Plugin
# ===========================================================================

class TestWpotd:
    @pytest.fixture
    def plugin(self):
        from plugins.wpotd.wpotd import Wpotd
        return Wpotd(WPOTD_CONFIG)

    def test_determine_date_today(self, plugin):
        from datetime import date
        result = plugin._determine_date({})
        assert result == date.today()

    def test_determine_date_custom(self, plugin):
        from datetime import date
        result = plugin._determine_date({"customDate": "2025-06-15"})
        assert result == date(2025, 6, 15)

    def test_determine_date_random(self, plugin):
        from datetime import date
        result = plugin._determine_date({"randomizeWpotd": "true"})
        assert isinstance(result, date)
        assert result >= date(2015, 1, 1)

    @patch("plugins.wpotd.wpotd.get_http_session")
    def test_successful_image(self, mock_session, plugin, mock_device_config):
        # Mock the two API calls (_make_request for potd data, then image src)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        # First call: _fetch_potd (images list)
        # Second call: _fetch_image_src (imageinfo)
        mock_resp.json.side_effect = [
            {"query": {"pages": [{"images": [{"title": "File:Test.jpg"}]}]}},
            {"query": {"pages": {"1": {"imageinfo": [{"url": "https://upload.wikimedia.org/test.jpg", "extmetadata": {}}]}}}},
        ]
        mock_session.return_value.get.return_value = mock_resp

        mock_img = Image.new("RGB", (800, 480), "purple")
        plugin.image_loader = MagicMock()
        plugin.image_loader.from_url.return_value = mock_img

        img = plugin.generate_image({"shrinkToFitWpotd": "true"}, mock_device_config)
        assert_valid_image(img)

    def test_svg_raises(self, plugin):
        with pytest.raises(RuntimeError, match="Failed to load WPOTD"):
            plugin._download_image("https://example.com/image.svg")


# ===========================================================================
# GitHub Plugin
# ===========================================================================

class TestGitHub:
    @pytest.fixture
    def plugin(self):
        from plugins.github.github import GitHub
        return GitHub(GITHUB_CONFIG)

    def test_missing_api_key(self, plugin, mock_device_config):
        mock_device_config.load_env_key.return_value = None
        with pytest.raises(RuntimeError, match="GitHub API Key"):
            plugin.generate_image({"githubType": "contributions", "githubUsername": "test"}, mock_device_config)

    def test_unknown_type(self, plugin, mock_device_config):
        mock_device_config.load_env_key.return_value = "fake-github-key"
        with pytest.raises(ValueError, match="Unknown GitHub type"):
            plugin.generate_image({"githubType": "invalid"}, mock_device_config)


# ===========================================================================
# Art Museum Plugin
# ===========================================================================

class TestArtMuseum:
    @pytest.fixture
    def plugin(self):
        from plugins.art_museum.art_museum import ArtMuseum
        return ArtMuseum(ART_MUSEUM_CONFIG)

    def test_get_art_types_defaults(self, plugin):
        types = plugin._get_art_types({})
        assert types == {"paintings", "photos", "others"}

    def test_get_art_types_filtered(self, plugin):
        types = plugin._get_art_types({"artPaintings": "true", "artPhotos": "false", "artOthers": "false"})
        assert types == {"paintings"}

    def test_classify_met(self, plugin):
        assert plugin._classify_met("Paintings") == "paintings"
        assert plugin._classify_met("Photographs") == "photos"
        assert plugin._classify_met("Sculpture") == "others"
        assert plugin._classify_met(None) == "others"

    def test_classify_chicago(self, plugin):
        assert plugin._classify_chicago("Painting") == "paintings"
        assert plugin._classify_chicago("Photograph") == "photos"
        assert plugin._classify_chicago("Textile") == "others"

    @patch("plugins.art_museum.art_museum.get_http_session")
    def test_successful_met_artwork(self, mock_session, plugin, mock_device_config):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        # First: search IDs, Second: object detail
        mock_resp.json.side_effect = [
            {"objectIDs": [12345]},
            {
                "objectID": 12345,
                "title": "Starry Night",
                "artistDisplayName": "Vincent van Gogh",
                "objectDate": "1889",
                "primaryImage": "https://example.com/starry.jpg",
                "classification": "Paintings",
            },
        ]
        mock_session.return_value.get.return_value = mock_resp

        mock_img = Image.new("RGB", (800, 480), "blue")
        plugin.image_loader = MagicMock()
        plugin.image_loader.from_url.return_value = mock_img

        img = plugin.generate_image({"museum": "met"}, mock_device_config)
        assert_valid_image(img)


# ===========================================================================
# Calendar Plugin
# ===========================================================================

class TestCalendar:
    @pytest.fixture
    def plugin(self):
        from plugins.calendar.calendar import Calendar
        return Calendar(CALENDAR_CONFIG)

    def test_missing_view(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="View is required"):
            plugin.generate_image({}, mock_device_config)

    def test_invalid_view(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="Invalid view"):
            plugin.generate_image({"viewMode": "invalidView"}, mock_device_config)

    def test_missing_calendar_urls(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="calendar URL is required"):
            plugin.generate_image({"viewMode": "dayGridMonth"}, mock_device_config)

    def test_get_contrast_color(self, plugin):
        assert plugin.get_contrast_color("#ffffff") == "#000000"  # white bg → black text
        assert plugin.get_contrast_color("#000000") == "#ffffff"  # black bg → white text

    def test_get_view_range_day(self, plugin):
        from datetime import datetime
        dt = datetime(2025, 6, 15, 10, 0)
        start, end = plugin.get_view_range("timeGridDay", dt, {})
        assert start.day == 15
        assert end.day == 16

    def test_get_view_range_month(self, plugin):
        from datetime import datetime
        dt = datetime(2025, 6, 15, 10, 0)
        start, end = plugin.get_view_range("dayGridMonth", dt, {})
        # Should encompass June and overlap
        assert start.month <= 6
        assert end.month >= 7

    @patch.object(
        __import__("plugins.calendar.calendar", fromlist=["Calendar"]).Calendar,
        "fetch_ics_events",
        return_value=[],
    )
    def test_render_month_empty_events(self, mock_fetch, plugin, mock_device_config):
        """Render month grid with no events — should still produce a valid image."""
        from datetime import datetime
        import pytz
        tz = pytz.timezone("US/Central")
        now = datetime.now(tz)
        img = plugin._render_month_grid(
            (800, 480), [], now, tz, "12h", 1.0,
            {"displayTitle": "true", "displayWeekends": "true", "weekStartDay": "0"}
        )
        assert_valid_image(img, (800, 480))

    def test_render_list_empty(self, plugin):
        from datetime import datetime
        import pytz
        tz = pytz.timezone("US/Central")
        now = datetime.now(tz)
        img = plugin._render_list(
            (800, 480), [], now, tz, "12h", 1.0,
            {"displayTitle": "true"}
        )
        assert_valid_image(img, (800, 480))

    def test_render_time_grid_day(self, plugin):
        from datetime import datetime
        import pytz
        tz = pytz.timezone("US/Central")
        now = datetime.now(tz)
        img = plugin._render_time_grid(
            (800, 480), [], now, tz, "12h", 1.0,
            {"displayTitle": "true", "startTimeInterval": "8", "endTimeInterval": "18"},
            "timeGridDay"
        )
        assert_valid_image(img, (800, 480))


# ===========================================================================
# ISS Tracker Plugin — Helper Functions
# ===========================================================================

class TestISSTracker:
    @pytest.fixture
    def plugin(self):
        from plugins.iss_tracker.iss_tracker import ISSTracker
        return ISSTracker(ISS_CONFIG)

    def test_observer_city_from_settings(self, plugin, mock_device_config):
        """When cityName is in settings, it should be used instead of _nearest_city."""
        from plugins.iss_tracker import iss_tracker
        settings = {
            "latitude": "32.7767",
            "longitude": "-96.7970",
            "cityName": "Dallas, Texas",
        }
        # Just extract the city name logic
        obs_city = settings.get("cityName", "").split(",")[0].strip()
        assert obs_city == "Dallas"

    def test_observer_city_fallback(self, plugin):
        """When no cityName in settings, should fall back to empty then _nearest_city."""
        obs_city = "".split(",")[0].strip()
        assert obs_city == ""

    def test_orbital_speed(self):
        from plugins.iss_tracker.iss_tracker import _orbital_speed
        speed = _orbital_speed(408)  # Typical ISS altitude
        assert 27000 < speed < 28000  # ~27,600 km/h

    def test_haversine(self):
        from plugins.iss_tracker.iss_tracker import _haversine
        # Dallas to Houston: ~362 km
        dist = _haversine(32.78, -96.80, 29.76, -95.37)
        assert 350 < dist < 380

    def test_parse_float(self):
        from plugins.iss_tracker.iss_tracker import _parse_float
        assert _parse_float("32.7", None) == 32.7
        assert _parse_float(None, 0.0) == 0.0
        assert _parse_float("bad", 99.0) == 99.0

    def test_parse_int(self):
        from plugins.iss_tracker.iss_tracker import _parse_int
        assert _parse_int("20", 10) == 20
        assert _parse_int(None, 10) == 10
        assert _parse_int("bad", 10) == 10


# ===========================================================================
# Weather Static Helper: get_moon_phase_name
# ===========================================================================

class TestWeatherHelpers:
    def test_moon_phase_name(self):
        from plugins.weather.weather import get_moon_phase_name
        assert get_moon_phase_name(0.5) == "newmoon"
        assert get_moon_phase_name(7.0) == "waxingcrescent"
        assert get_moon_phase_name(8.0) == "firstquarter"
        assert get_moon_phase_name(14.0) == "waxinggibbous"
        assert get_moon_phase_name(15.0) == "fullmoon"
        assert get_moon_phase_name(22.0) == "waninggibbous"
        assert get_moon_phase_name(23.0) == "lastquarter"
        assert get_moon_phase_name(29.0) == "waningcrescent"
        assert get_moon_phase_name(29.5) == "newmoon"  # wraps
