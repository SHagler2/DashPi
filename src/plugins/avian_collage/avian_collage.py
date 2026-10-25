"""BirdNet Collage plugin — shows the AvianVisitors bird collage from a BirdNET-Pi station.

The collage is a JS page that takes a Zero 2 W 70-120s to render in headless
Chromium, so the bird station renders it to a PNG on a timer and this plugin
only downloads that PNG. See README.md in this folder for the station setup.
"""

from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import get_font
from utils.http_client import get_http_session
from PIL import ImageDraw
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import logging
import requests

logger = logging.getLogger(__name__)

DEFAULT_STATION_URL = "http://birdnet.local"
# Hours -> the PNG the station's render timer writes (station/render.sh)
WINDOWS = {"1": "collage-1h.png", "12": "collage-12h.png", "24": "collage-24h.png", "168": "collage-168h.png"}
DEFAULT_WINDOW = "24"
MAX_BYTES = 10 * 1024 * 1024
STALE_AFTER_SECONDS = 2 * 60 * 60
PAPER_MIN = 245  # channel values at or above this become pure white
DEFAULT_TIMEZONE = "America/Chicago"


class AvianCollage(BasePlugin):
    """Downloads the station's pre-rendered collage PNG and fits it to the display."""

    def generate_image(self, settings, device_config):
        """Fetch the collage PNG for the chosen time window and render it for display."""
        station_url = (settings.get('stationUrl') or DEFAULT_STATION_URL).strip().rstrip('/')
        parsed = urlparse(station_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise RuntimeError(f"Invalid bird station URL: {station_url}")

        window = str(settings.get('window') or DEFAULT_WINDOW)
        if window not in WINDOWS:
            raise RuntimeError(f"Unknown time window: {window}")
        dark = settings.get("darkMode") in ("on", True)
        url = f"{station_url}/dashpi/{WINDOWS[window]}"
        if dark:
            # The station renders a dark copy with AvianVisitors' own charcoal theme.
            url = url.replace(".png", "-dark.png")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        # Fetched with the shared session instead of image_loader.from_url, which
        # blocks private addresses; the bird station is always on the LAN.
        data, modified = self._download(url, parsed.hostname)
        try:
            image = self.image_loader.from_bytesio(data, dimensions,
                                                   fit_mode=settings.get("fitMode", "fill"))
        finally:
            data.close()

        if not image:
            raise RuntimeError("Collage image could not be decoded, please check logs.")
        if not dark:
            image = self._whiten_paper(image)
        if modified and (datetime.now(timezone.utc) - modified).total_seconds() > STALE_AFTER_SECONDS:
            logger.warning(f"BirdNet Collage: station image last updated {modified.isoformat()}; "
                           "check the render timer on the bird station")
            image = self._add_stale_notice(image, modified, device_config)
        return image

    @staticmethod
    def _whiten_paper(image):
        """Snap the collage's off-white paper (~#fcfcfb) to pure white.

        Invisible on an LCD, but e-ink panels dither the slight tint into
        coloured speckle across the whole background.
        """
        lut = [255 if v >= PAPER_MIN else v for v in range(256)]
        return image.convert("RGB").point(lut * 3)

    def _download(self, url, host):
        try:
            response = get_http_session().get(url, timeout=20)
        except (requests.ConnectionError, requests.Timeout):
            raise RuntimeError(f"Bird station unreachable at {host}.")
        except requests.RequestException as e:
            logger.error(f"BirdNet Collage: request to {url} failed: {e}")
            raise RuntimeError(f"Bird station request failed: {host}")

        if response.status_code == 404:
            if url.endswith("-dark.png"):
                raise RuntimeError("Dark collage not found on the bird station. "
                                   "Update render.sh there (see the plugin README).")
            raise RuntimeError("Collage image not found on the bird station. "
                               "Is the render timer set up?")
        if response.status_code != 200:
            raise RuntimeError(f"Bird station returned HTTP {response.status_code}.")
        if len(response.content) > MAX_BYTES:
            raise RuntimeError("Collage image is too large.")

        return BytesIO(response.content), self._parse_last_modified(response.headers.get("Last-Modified"))

    @staticmethod
    def _parse_last_modified(value):
        """The station refreshes the PNG's timestamp every 5 minutes, so its age shows whether it's working."""
        if not value:
            return None
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _add_stale_notice(image, modified, device_config):
        """Overlay a "not updated since" strip along the bottom; the old collage stays visible above it."""
        try:
            tz = ZoneInfo(device_config.get_config("timezone") or DEFAULT_TIMEZONE)
        except (KeyError, TypeError, ValueError):
            tz = ZoneInfo(DEFAULT_TIMEZONE)
        local = modified.astimezone(tz)
        clock = (local.strftime("%H:%M") if device_config.get_config("time_format", default="12h") == "24h"
                 else local.strftime("%I:%M %p").lstrip("0"))
        when = clock if local.date() == datetime.now(tz).date() else f"{local.strftime('%b')} {local.day}, {clock}"
        text = f"Bird station not updated since {when}"

        width, height = image.size
        bar = max(28, int(height * 0.06))
        font = get_font("Jost", int(bar * 0.6), "bold")
        draw = ImageDraw.Draw(image)
        # Plain black on white so e-ink panels draw it without dithering.
        draw.rectangle((0, height - bar, width, height), fill=(255, 255, 255))
        draw.line((0, height - bar, width, height - bar), fill=(0, 0, 0), width=2)
        draw.text((width // 2, height - bar // 2), text, font=font, fill=(0, 0, 0), anchor="mm")
        return image
