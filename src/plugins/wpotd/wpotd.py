"""
Wpotd Plugin for DashPi
This plugin fetches the Wikipedia Picture of the Day (Wpotd) from Wikipedia's API
and displays it on the DashPi device.

It supports optional manual date selection or random dates and can resize the image to fit the device's dimensions.

Wikipedia API Documentation: https://www.mediawiki.org/wiki/API:Main_page
Picture of the Day example: https://www.mediawiki.org/wiki/API:Picture_of_the_day_viewer
Github Repository: https://github.com/wikimedia/mediawiki-api-demos/tree/master/apps/picture-of-the-day-viewer
Wikimedia requires a User Agent header for API requests, which is set in the SESSION headers:
https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy

Flow:

1. Fetch the date to use for the Picture of the Day (POTD) based on settings. (_determine_date)
2. Make an API request to fetch the POTD data for that date. (_fetch_potd)
3. Extract the image filename from the response. (_fetch_potd)
4. Make another API request to get the image URL. (_fetch_image_src)
5. Download the image from the URL. (_download_image)
6. Optionally resize the image to fit the device dimensions. (_shrink_to_fit))
"""

from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from io import BytesIO
from utils.http_client import get_http_session
import logging
from random import randint
from datetime import datetime, timedelta, date
from typing import Dict, Any

logger = logging.getLogger(__name__)

class Wpotd(BasePlugin):
    """Fetches Wikipedia's Picture of the Day and renders it with an optional title overlay."""

    HEADERS = {'User-Agent': 'DashPi/1.0'}
    API_URL = "https://en.wikipedia.org/w/api.php"

    def generate_settings_template(self) -> Dict[str, Any]:
        template_params = super().generate_settings_template()
        template_params['style_settings'] = False
        return template_params

    def generate_image(self, settings: Dict[str, Any], device_config: Dict[str, Any]) -> Image.Image:
        """Fetch and render Wikipedia's Picture of the Day for the configured date."""
        logger.info("=== Wikipedia POTD Plugin: Starting image generation ===")

        # Get dimensions early for retry logic
        max_width, max_height = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            max_width, max_height = max_height, max_width
            logger.debug(f"Vertical orientation detected, dimensions: {max_width}x{max_height}")
        dimensions = (max_width, max_height)

        shrink_to_fit = settings.get("shrinkToFitWpotd") == "true"
        fit_mode = settings.get("fitMode", "fit")  # Default to 'fit' for letterbox
        is_random_mode = settings.get("randomizeWpotd") == "true"

        # Retry logic for random mode - try up to 5 different dates if one fails
        max_attempts = 5 if is_random_mode else 1
        last_error = None

        for attempt in range(max_attempts):
            try:
                datetofetch = self._determine_date(settings)
                logger.info(f"Fetching Wikipedia Picture of the Day for: {datetofetch}" +
                           (f" (attempt {attempt + 1}/{max_attempts})" if max_attempts > 1 else ""))

                data = self._fetch_potd(datetofetch)
                picurl = data["image_src"]
                title = data.get("title", "")
                logger.info(f"Image URL: {picurl}")

                logger.info(
                    f"Wikipedia POTD display settings: shrink_to_fit={'enabled' if shrink_to_fit else 'disabled'}, "
                    f"fit_mode={fit_mode}, "
                    f"{'using adaptive loader with fit_mode' if shrink_to_fit else 'downloading original size'}"
                )

                image = self._download_image(
                    picurl,
                    dimensions=dimensions,
                    resize=shrink_to_fit,
                    fit_mode=fit_mode,
                )
                if image is None:
                    raise RuntimeError("Image download returned None (possibly too large)")

                if shrink_to_fit:
                    logger.info(f"Image resized to fit device dimensions: {max_width}x{max_height}")

                # Add title overlay
                if title:
                    image = self._add_title_overlay(image, title)
                    logger.info(f"Added title overlay: {title}")

                logger.info("=== Wikipedia POTD Plugin: Image generation complete ===")
                return image

            except Exception as e:
                last_error = e
                if is_random_mode and attempt < max_attempts - 1:
                    logger.warning(f"Failed to load WPOTD for {datetofetch}: {e}. Trying another random date...")
                    continue
                else:
                    break

        logger.error(f"Failed to download WPOTD image after {max_attempts} attempt(s)")
        raise RuntimeError(f"Failed to download WPOTD image: {last_error}")

    def _determine_date(self, settings: Dict[str, Any]) -> date:
        if settings.get("randomizeWpotd") == "true":
            start = datetime(2015, 1, 1)
            delta_days = (datetime.today() - start).days
            return (start + timedelta(days=randint(0, delta_days))).date()
        elif settings.get("customDate"):
            return datetime.strptime(settings["customDate"], "%Y-%m-%d").date()
        else:
            return datetime.today().date()

    def _download_image(self, url: str, dimensions: tuple = None, resize: bool = False, fit_mode: str = 'fit') -> Image.Image:
        """
        Download image from URL, optionally resizing with adaptive loader.

        Args:
            url: Image URL
            dimensions: Target dimensions if resizing
            resize: Whether to use adaptive resizing
            fit_mode: 'fill' (crop to fill) or 'fit' (letterbox to fit)
        """
        try:
            if url.lower().endswith(".svg"):
                logger.warning("SVG format is not supported by Pillow. Skipping image download.")
                raise RuntimeError("Unsupported image format: SVG.")

            if resize and dimensions:
                # Use adaptive loader for memory-efficient processing
                return self.image_loader.from_url(url, dimensions, timeout_ms=10000, headers=self.HEADERS, fit_mode=fit_mode)
            else:
                # Original behavior: download without resizing
                session = get_http_session()
                response = session.get(url, headers=self.HEADERS, timeout=10)
            response.raise_for_status()
            return Image.open(BytesIO(response.content))

        except UnidentifiedImageError as e:
            logger.error(f"Unsupported image format at {url}: {str(e)}")
            raise RuntimeError("Unsupported image format.")
        except Exception as e:
            logger.error(f"Failed to load WPOTD image from {url}: {str(e)}")
            raise RuntimeError("Failed to load WPOTD image.")

    def _fetch_potd(self, cur_date: date) -> Dict[str, Any]:
        title = f"Template:POTD/{cur_date.isoformat()}"
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "images",
            "titles": title
        }

        data = self._make_request(params)
        try:
            filename = data["query"]["pages"][0]["images"][0]["title"]
        except (KeyError, IndexError) as e:
            logger.error(f"Failed to retrieve POTD filename for {cur_date}: {e}")
            raise RuntimeError("Failed to retrieve POTD filename.")

        image_data = self._fetch_image_src(filename)

        return {
            "filename": filename,
            "image_src": image_data["url"],
            "title": image_data.get("title", ""),
            "image_page_url": f"https://en.wikipedia.org/wiki/{title}",
            "date": cur_date
        }

    def _fetch_image_src(self, filename: str) -> Dict[str, str]:
        params = {
            "action": "query",
            "format": "json",
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "titles": filename
        }
        data = self._make_request(params)
        try:
            page = next(iter(data["query"]["pages"].values()))
            imageinfo = page["imageinfo"][0]
            url = imageinfo["url"]

            # Try to get a readable title/description from metadata
            title = ""
            extmetadata = imageinfo.get("extmetadata", {})

            # Try ObjectName first (usually the title), then ImageDescription
            if "ObjectName" in extmetadata:
                title = extmetadata["ObjectName"].get("value", "")
            elif "ImageDescription" in extmetadata:
                title = extmetadata["ImageDescription"].get("value", "")

            # Remove all HTML tags and clean up
            if title:
                import re
                from html import unescape

                # Remove HTML tags
                title = re.sub('<[^<]+?>', '', title)
                # Decode HTML entities
                title = unescape(title)
                # Remove any remaining wikitext/labels (like "label QS:Len")
                title = re.sub(r'label\s+QS:[^"]*"([^"]*)".*', r'\1', title)
                # Remove extra quotes and whitespace
                title = title.replace('"', '').strip()
                title = ' '.join(title.split()).strip()

            # Truncate if too long
            if len(title) > 80:
                title = title[:77] + "..."

            return {"url": url, "title": title}
        except (KeyError, IndexError, StopIteration) as e:
            logger.error(f"Failed to retrieve image info for {filename}: {e}")
            raise RuntimeError("Failed to retrieve image info.")

    def _make_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            session = get_http_session()
            response = session.get(self.API_URL, params=params, headers=self.HEADERS, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Wikipedia API request failed with params {params}: {str(e)}")
            raise RuntimeError("Wikipedia API request failed.")

    def _add_title_overlay(self, image: Image.Image, title: str) -> Image.Image:
        """Add title text overlay at the bottom of the image with contrasting background."""
        # Create a copy to avoid modifying the original
        img_with_overlay = image.copy()
        draw = ImageDraw.Draw(img_with_overlay, 'RGBA')

        width, height = img_with_overlay.size

        # Try to use a nice font, fall back to default if not available
        try:
            font_size = max(16, int(height * 0.018))  # 1.8% of image height
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
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
