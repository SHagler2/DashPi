"""BirdNet Collage plugin — shows the AvianVisitors bird collage from a BirdNET-Pi station.

The collage is a JS page that takes a Zero 2 W 70-120s to render in headless
Chromium, so the bird station renders it to a PNG on a timer and this plugin
only downloads that PNG. See README.md in this folder for the station setup.
"""

from plugins.base_plugin.base_plugin import BasePlugin
from utils.http_client import get_http_session
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import urlparse
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
        url = f"{station_url}/dashpi/{WINDOWS[window]}"

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        # Fetched with the shared session instead of image_loader.from_url, which
        # blocks private addresses; the bird station is always on the LAN.
        data = self._download(url, parsed.hostname)
        try:
            image = self.image_loader.from_bytesio(data, dimensions,
                                                   fit_mode=settings.get("fitMode", "fill"))
        finally:
            data.close()

        if not image:
            raise RuntimeError("Collage image could not be decoded, please check logs.")
        return self._whiten_paper(image)

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
            raise RuntimeError("Collage image not found on the bird station. "
                               "Is the render timer set up?")
        if response.status_code != 200:
            raise RuntimeError(f"Bird station returned HTTP {response.status_code}.")
        if len(response.content) > MAX_BYTES:
            raise RuntimeError("Collage image is too large.")

        self._warn_if_stale(response.headers.get("Last-Modified"))
        return BytesIO(response.content)

    @staticmethod
    def _warn_if_stale(last_modified):
        """Log when the station's render timer seems to have stopped; the image is still shown."""
        if not last_modified:
            return
        try:
            age = (datetime.now(timezone.utc) - parsedate_to_datetime(last_modified)).total_seconds()
        except (TypeError, ValueError):
            return
        if age > STALE_AFTER_SECONDS:
            logger.warning(f"BirdNet Collage: station image is {int(age // 60)} min old; "
                           "check the render timer on the bird station")
