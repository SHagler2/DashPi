"""
APOD Plugin for DashPi
Fetches the Astronomy Picture of the Day from NASA's science.nasa.gov endpoint
and displays it on the DashPi device.
"""

from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from utils.app_utils import get_font
from utils.http_client import get_http_session
import logging
import re
from random import randint
from datetime import datetime, timedelta

API_BASE = "https://science.nasa.gov/wp-json/wp/v2/apod-basic"
API_TIMEOUT = 15

logger = logging.getLogger(__name__)


def _strip_html(text):
    """Remove HTML tags and entities from a string."""
    text = re.sub('<[^<]+?>', '', text)
    text = re.sub(r'&[a-zA-Z]+;', '', text)
    text = re.sub(r'&#\d+;', '', text)
    return ' '.join(text.split()).strip()


def _date_to_apod_slug(dt):
    """Convert a datetime to the YYMMDD slug used by the apod-basic endpoint."""
    return dt.strftime("%y%m%d")


def _constrain_image_url(hdurl, max_width=1600):
    """Rewrite the dynamic image URL to cap width, avoiding huge downloads."""
    if "dynamicimage" in hdurl and "w=" in hdurl:
        return re.sub(r'w=\d+', f'w={max_width}', hdurl)
    return hdurl


class Apod(BasePlugin):
    """Fetches NASA's Astronomy Picture of the Day and renders it for display."""

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = False
        return template_params

    def generate_image(self, settings, device_config):
        """Fetch and render the APOD image for the configured or random date."""
        logger.info("=== APOD Plugin: Starting image generation ===")

        max_retries = 10
        is_random = settings.get("randomizeApod") == "true"
        custom_date = settings.get("customDate")

        session = get_http_session()
        data = None

        for attempt in range(max_retries):
            if is_random:
                start = datetime(2015, 1, 1)
                end = datetime.today()
                delta_days = (end - start).days
                target_date = start + timedelta(days=randint(0, delta_days))
            elif custom_date:
                target_date = datetime.strptime(custom_date, "%Y-%m-%d") - timedelta(days=attempt)
            else:
                target_date = datetime.today() - timedelta(days=attempt)

            slug = _date_to_apod_slug(target_date)
            url = f"{API_BASE}/{slug}"
            logger.info(f"Fetching APOD for {target_date.strftime('%Y-%m-%d')} (attempt {attempt + 1}): {url}")

            response = session.get(url, timeout=API_TIMEOUT)

            if response.status_code != 200:
                logger.error(f"APOD API error (status {response.status_code})")
                continue

            data = response.json()
            if isinstance(data, list):
                data = data[0] if data else None

            if not data:
                logger.warning(f"Empty response for {slug}")
                continue

            if data.get("media_type") == "image":
                logger.info(f"Found APOD image: {data.get('title')}")
                break
            else:
                logger.warning(f"APOD on {slug} is '{data.get('media_type')}', not an image. Trying another date...")
        else:
            logger.error(f"Failed to find an APOD image after {max_retries} attempts")
            raise RuntimeError(f"Could not find an APOD image after {max_retries} attempts.")

        image_url = data.get("hdurl")
        if not image_url:
            raise RuntimeError("APOD entry has no image URL.")

        image_url = _constrain_image_url(image_url)
        logger.info(f"APOD image URL: {image_url}")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        fit_mode = settings.get("fitMode", "fit")

        image = self.image_loader.from_url(image_url, dimensions, timeout_ms=40000, fit_mode=fit_mode)

        if not image:
            logger.error("Failed to load APOD image")
            raise RuntimeError("Failed to load APOD image.")

        title = _strip_html(data.get("title", ""))
        if title:
            if len(title) > 80:
                title = title[:77] + "..."
            image = self._add_title_overlay(image, title)
            logger.info(f"Added title overlay: {title}")

        logger.info("=== APOD Plugin: Image generation complete ===")
        return image

    def _add_title_overlay(self, image: Image.Image, title: str) -> Image.Image:
        """Add title text overlay at the bottom of the image with contrasting background."""
        img_with_overlay = image.copy()
        draw = ImageDraw.Draw(img_with_overlay, 'RGBA')

        width, height = img_with_overlay.size

        try:
            font_size = max(16, int(height * 0.018))
            font = get_font("Jost", font_size, "bold")
        except Exception:
            font = ImageFont.load_default()
            logger.warning("Could not load custom font, using default")

        bbox = draw.textbbox((0, 0), title, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        padding = max(10, int(height * 0.01))

        text_x = (width - text_width) // 2
        text_y = height - text_height - padding

        bg_top = text_y - padding
        bg_bottom = height
        draw.rectangle(
            [(0, bg_top), (width, bg_bottom)],
            fill=(0, 0, 0, 180)
        )

        draw.text((text_x, text_y), title, font=font, fill=(255, 255, 255, 255),
                  stroke_width=2, stroke_fill=(0, 0, 0, 255))

        return img_with_overlay
