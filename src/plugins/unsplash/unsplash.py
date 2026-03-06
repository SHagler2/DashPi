"""Unsplash plugin — displays a random photo from Unsplash based on search or collection filters."""

from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import get_font
from utils.image_loader import _is_low_resource_device
from utils.http_client import get_http_session
from PIL import ImageDraw, ImageFont
import logging
import random

logger = logging.getLogger(__name__)

class Unsplash(BasePlugin):
    """Fetches a random photo from the Unsplash API and renders it with an optional credit overlay."""

    def generate_image(self, settings, device_config):
        """Fetch a random Unsplash photo matching the configured filters and render it."""
        logger.info("=== Unsplash Plugin: Starting image generation ===")

        access_key = device_config.load_env_key("UNSPLASH_ACCESS_KEY")
        if not access_key:
            logger.error("Unsplash Access Key not found in environment")
            raise RuntimeError("'Unsplash Access Key' not found.")

        search_query = settings.get('search_query')
        collections = settings.get('collections')
        content_filter = settings.get('content_filter', 'low')
        color = settings.get('color')
        orientation = settings.get('orientation')

        # Automatically determine image size based on device capabilities
        is_low_resource = _is_low_resource_device()
        image_size = 'regular' if is_low_resource else 'full'
        logger.info(f"Device type: {'low-resource' if is_low_resource else 'standard'}, using image size: '{image_size}'")

        logger.info(f"Settings: image_size='{image_size}', content_filter='{content_filter}'")
        if search_query:
            logger.info(f"Search query: '{search_query}'")
        if collections:
            logger.info(f"Collections: {collections}")
        if color:
            logger.debug(f"Color filter: {color}")
        if orientation:
            logger.debug(f"Orientation: {orientation}")

        params = {
            'client_id': access_key,
            'content_filter': content_filter,
            'per_page': 100,
        }

        if search_query:
            url = "https://api.unsplash.com/search/photos"
            params['query'] = search_query
            logger.debug(f"Using search endpoint: {url}")
        else:
            url = "https://api.unsplash.com/photos/random"
            logger.debug(f"Using random photo endpoint: {url}")

        if collections:
            params['collections'] = collections
        if color:
            params['color'] = color
        if orientation:
            params['orientation'] = orientation

        try:
            logger.debug("Fetching image from Unsplash API...")
            session = get_http_session()
            response = session.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            if search_query:
                results = data.get("results")
                if not results:
                    logger.warning(f"No images found for search query: '{search_query}'")
                    raise RuntimeError("No images found for the given search query.")
                logger.info(f"Found {len(results)} images matching search query")
                # Use selected image size (with automatic downgrade for low-RAM devices)
                selected_photo = random.choice(results)
                image_url = selected_photo["urls"].get(image_size) or selected_photo["urls"].get("regular")
                photo_data = selected_photo
                logger.debug(f"Selected random image from {len(results)} results")
            else:
                # Use selected image size (with automatic downgrade for low-RAM devices)
                image_url = data["urls"].get(image_size) or data["urls"].get("regular")
                photo_data = data
                logger.debug("Retrieved random image URL")

            if not image_url:
                raise RuntimeError("No image URL found in Unsplash API response.")

            # Extract photo metadata
            description = photo_data.get("description") or photo_data.get("alt_description") or ""
            photographer = ""
            user_data = photo_data.get("user")
            if user_data:
                photographer = user_data.get("name", "")

        except (KeyError, IndexError) as e:
            logger.error(f"Error parsing Unsplash API response: {e}")
            raise RuntimeError("Failed to parse Unsplash API response, please check logs.")
        except Exception as e:
            logger.error(f"Error fetching image from Unsplash API: {e}")
            raise RuntimeError("Failed to fetch image from Unsplash API, please check logs.")


        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
            logger.debug(f"Vertical orientation detected, dimensions: {dimensions[0]}x{dimensions[1]}")

        logger.info(f"Fetching image (size: {image_size}): {image_url}")

        # Get fit mode setting (default to 'fit' for letterbox)
        fit_mode = settings.get("fitMode", "fit")
        logger.debug(f"Fit mode: {fit_mode}")

        # Use adaptive image loader for memory-efficient processing
        image = self.image_loader.from_url(image_url, dimensions, timeout_ms=40000, fit_mode=fit_mode)

        if not image:
            logger.error("Failed to load and process image")
            raise RuntimeError("Failed to load image, please check logs.")

        # Add photo info overlay if enabled
        if settings.get("showPhotoInfo") == "true" and (description or photographer):
            image = self._add_photo_overlay(image, description, photographer)

        logger.info("=== Unsplash Plugin: Image generation complete ===")
        return image

    def _add_photo_overlay(self, image, description, photographer):
        """Add photo info overlay at the bottom of the image."""
        img_with_overlay = image.copy()
        draw = ImageDraw.Draw(img_with_overlay, 'RGBA')

        width, height = img_with_overlay.size

        # Build overlay text
        if description and photographer:
            # Truncate description if needed to leave room for photographer
            max_desc_len = 60
            if len(description) > max_desc_len:
                description = description[:max_desc_len - 3] + "..."
            overlay_text = f"{description} — by {photographer}"
        elif photographer:
            overlay_text = f"by {photographer}"
        else:
            overlay_text = description[:80] if len(description) > 80 else description

        try:
            font_size = max(16, int(height * 0.018))
            font = get_font("Jost", font_size, "bold")
        except Exception:
            font = ImageFont.load_default()
            logger.warning("Could not load custom font, using default")

        # Calculate text size and position
        bbox = draw.textbbox((0, 0), overlay_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        padding = max(10, int(height * 0.01))
        text_x = (width - text_width) // 2
        text_y = height - text_height - padding

        # Semi-transparent black background
        draw.rectangle(
            [(0, text_y - padding), (width, height)],
            fill=(0, 0, 0, 180)
        )

        # White text with black outline for contrast
        draw.text((text_x, text_y), overlay_text, font=font, fill=(255, 255, 255, 255),
                  stroke_width=2, stroke_fill=(0, 0, 0, 255))

        logger.info(f"Added photo overlay: {overlay_text}")
        return img_with_overlay
