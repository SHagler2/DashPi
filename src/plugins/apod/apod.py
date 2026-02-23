"""
APOD Plugin for DashPi
This plugin fetches the Astronomy Picture of the Day (APOD) from NASA's API
and displays it on the DashPi device. It supports optional manual date selection or random dates.
For the API key, set `NASA_SECRET={API_KEY}` in your .env file.
"""

from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from utils.app_utils import get_font
from utils.http_client import get_http_session
import logging
from random import randint
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class Apod(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "NASA",
            "expected_key": "NASA_SECRET"
        }
        template_params['style_settings'] = False
        return template_params

    def generate_image(self, settings, device_config):
        logger.info("=== APOD Plugin: Starting image generation ===")

        api_key = device_config.load_env_key("NASA_SECRET")
        if not api_key:
            logger.error("NASA API Key not configured")
            raise RuntimeError("NASA API Key not configured.")

        # Retry up to 10 times to find an image (not video)
        max_retries = 10
        is_random = settings.get("randomizeApod") == "true"
        custom_date = settings.get("customDate")

        for attempt in range(max_retries):
            params = {"api_key": api_key}

            # Determine date to fetch
            if is_random:
                start = datetime(2015, 1, 1)
                end = datetime.today()
                delta_days = (end - start).days
                random_date = start + timedelta(days=randint(0, delta_days))
                params["date"] = random_date.strftime("%Y-%m-%d")
                logger.info(f"Fetching random APOD from date: {params['date']} (attempt {attempt + 1})")
            elif custom_date:
                # If custom date specified, go back day by day on retries
                target_date = datetime.strptime(custom_date, "%Y-%m-%d") - timedelta(days=attempt)
                params["date"] = target_date.strftime("%Y-%m-%d")
                logger.info(f"Fetching APOD from date: {params['date']} (attempt {attempt + 1})")
            else:
                # Fetching today's APOD, go back day by day on retries
                target_date = datetime.today() - timedelta(days=attempt)
                params["date"] = target_date.strftime("%Y-%m-%d")
                logger.info(f"Fetching APOD from date: {params['date']} (attempt {attempt + 1})")

            logger.debug("Requesting NASA APOD API...")
            session = get_http_session()
            response = session.get("https://api.nasa.gov/planetary/apod", params=params)

            if response.status_code != 200:
                logger.error(f"NASA API error (status {response.status_code}): {response.text}")
                continue  # Try next date

            data = response.json()
            logger.debug(f"APOD API response received: {data.get('title', 'No title')}")

            # Check if it's an image
            if data.get("media_type") == "image":
                logger.info(f"Found APOD image on date: {params['date']}")
                break  # Success! Exit retry loop
            else:
                logger.warning(f"APOD on {params['date']} is a '{data.get('media_type')}', not an image. Trying another date...")
        else:
            # All retries exhausted
            logger.error(f"Failed to find an APOD image after {max_retries} attempts")
            raise RuntimeError(f"Could not find an APOD image after {max_retries} attempts.")

        image_url = data.get("hdurl") or data.get("url")
        logger.info(f"APOD image URL: {image_url}")
        logger.debug(f"Using {'HD URL' if data.get('hdurl') else 'standard URL'}")

        # Get target dimensions
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
            logger.debug(f"Vertical orientation detected, dimensions: {dimensions[0]}x{dimensions[1]}")

        # Get fit mode setting (default to 'fit' for letterbox)
        fit_mode = settings.get("fitMode", "fit")
        logger.debug(f"Fit mode: {fit_mode}")

        # Use adaptive image loader for memory-efficient processing
        image = self.image_loader.from_url(image_url, dimensions, timeout_ms=40000, fit_mode=fit_mode)

        if not image:
            logger.error("Failed to load APOD image")
            raise RuntimeError("Failed to load APOD image.")

        # Add title overlay
        title = data.get("title", "")
        if title:
            # Clean up any HTML if present
            import re
            title = re.sub('<[^<]+?>', '', title)
            title = re.sub(r'&[a-zA-Z]+;', '', title)
            title = ' '.join(title.split()).strip()
            # Truncate if too long
            if len(title) > 80:
                title = title[:77] + "..."

            image = self._add_title_overlay(image, title)
            logger.info(f"Added title overlay: {title}")

        logger.info("=== APOD Plugin: Image generation complete ===")
        return image

    def _add_title_overlay(self, image: Image.Image, title: str) -> Image.Image:
        """Add title text overlay at the bottom of the image with contrasting background."""
        # Create a copy to avoid modifying the original
        img_with_overlay = image.copy()
        draw = ImageDraw.Draw(img_with_overlay, 'RGBA')

        width, height = img_with_overlay.size

        # Try to use a nice font, fall back to default if not available
        try:
            font_size = max(16, int(height * 0.018))  # 1.8% of image height
            font = get_font("Jost", font_size, "bold")
        except Exception:
            font = ImageFont.load_default()
            logger.warning("Could not load custom font, using default")

        # Calculate text size and position
        bbox = draw.textbbox((0, 0), title, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Add padding
        padding = max(10, int(height * 0.01))

        # Position at bottom of image
        text_x = (width - text_width) // 2
        text_y = height - text_height - padding

        # Draw semi-transparent black rectangle background
        bg_top = text_y - padding
        bg_bottom = height
        draw.rectangle(
            [(0, bg_top), (width, bg_bottom)],
            fill=(0, 0, 0, 180)  # Black with 70% opacity
        )

        # Draw white text with black outline for extra contrast
        outline_width = 2
        for adj_x in range(-outline_width, outline_width + 1):
            for adj_y in range(-outline_width, outline_width + 1):
                if adj_x != 0 or adj_y != 0:
                    draw.text((text_x + adj_x, text_y + adj_y), title, font=font, fill=(0, 0, 0, 255))

        # Draw white text
        draw.text((text_x, text_y), title, font=font, fill=(255, 255, 255, 255))

        return img_with_overlay
