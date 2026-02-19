"""
Art Museum Plugin for DashPi
Displays random artworks from the Metropolitan Museum of Art and the
Art Institute of Chicago. No API keys required.
"""

from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw, ImageFont
from utils.http_client import get_http_session
import logging
import random

logger = logging.getLogger(__name__)

MET_SEARCH_URL = "https://collectionapi.metmuseum.org/public/collection/v1/search"
MET_OBJECT_URL = "https://collectionapi.metmuseum.org/public/collection/v1/objects"
CHICAGO_API_URL = "https://api.artic.edu/api/v1/artworks"
CHICAGO_IIIF_URL = "https://www.artic.edu/iiif/2"


class ArtMuseum(BasePlugin):
    def __init__(self, config, **deps):
        super().__init__(config, **deps)
        self._met_ids = None  # Cached list of Met object IDs with images

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {"required": False}
        template_params['style_settings'] = False
        return template_params

    def generate_image(self, settings, device_config):
        logger.info("=== Art Museum Plugin: Starting ===")

        museum = settings.get("museum", "both")
        show_title = settings.get("showTitle", "true") != "false"
        fit_mode = settings.get("fitMode", "fit")
        art_types = self._get_art_types(settings)

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        # Pick source
        if museum == "both":
            source = random.choice(["met", "chicago"])
        else:
            source = museum

        logger.info(f"Fetching artwork from: {source} (types: {art_types})")

        if source == "met":
            artwork = self._fetch_met_artwork(art_types)
        else:
            artwork = self._fetch_chicago_artwork(art_types)

        logger.info(f"Artwork: '{artwork['title']}' by {artwork['artist']}")

        # Load and resize image
        image = self.image_loader.from_url(
            artwork['image_url'], dimensions, timeout_ms=40000, fit_mode=fit_mode
        )

        if not image:
            raise RuntimeError("Failed to load artwork image.")

        # Add title overlay
        if show_title:
            title = artwork['title'] or "Untitled"
            if len(title) > 80:
                title = title[:77] + "..."

            subtitle = artwork['artist'] or ""
            if artwork.get('date'):
                subtitle = f"{subtitle}, {artwork['date']}" if subtitle else artwork['date']

            image = self._add_title_overlay(image, title, subtitle)

        logger.info("=== Art Museum Plugin: Complete ===")
        return image

    def _get_art_types(self, settings):
        """Get enabled art type filters from settings."""
        types = set()
        if settings.get("artPaintings", "true") != "false":
            types.add("paintings")
        if settings.get("artPhotos", "true") != "false":
            types.add("photos")
        if settings.get("artOthers", "true") != "false":
            types.add("others")
        # Default to all if none selected
        if not types:
            types = {"paintings", "photos", "others"}
        return types

    def _classify_met(self, classification):
        """Classify a Met artwork by its classification field."""
        if not classification:
            return "others"
        cl = classification.lower()
        if "paint" in cl:
            return "paintings"
        if "photograph" in cl or "photo" in cl:
            return "photos"
        return "others"

    def _classify_chicago(self, artwork_type):
        """Classify a Chicago artwork by its artwork_type_title field."""
        if not artwork_type:
            return "others"
        at = artwork_type.lower()
        if "paint" in at:
            return "paintings"
        if "photograph" in at or "photo" in at:
            return "photos"
        return "others"

    def _fetch_met_artwork(self, art_types):
        """Fetch a random artwork from the Metropolitan Museum of Art."""
        session = get_http_session()

        # Cache the list of object IDs with images
        if self._met_ids is None:
            logger.info("Fetching Met Museum object ID list (first time)...")
            resp = session.get(MET_SEARCH_URL, params={
                "hasImages": "true",
                "q": "*"
            }, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            self._met_ids = data.get("objectIDs", [])
            logger.info(f"Cached {len(self._met_ids)} Met object IDs")

        if not self._met_ids:
            raise RuntimeError("No artworks found in Met Museum API.")

        # Try random objects until we find one with an image and matching type
        for attempt in range(20):
            obj_id = random.choice(self._met_ids)
            try:
                resp = session.get(f"{MET_OBJECT_URL}/{obj_id}", timeout=15)
                resp.raise_for_status()
                obj = resp.json()

                image_url = obj.get("primaryImage", "")
                if not image_url:
                    logger.debug(f"Met object {obj_id} has no primaryImage, retrying...")
                    continue

                classification = obj.get("classification", "")
                art_type = self._classify_met(classification)
                if art_type not in art_types:
                    logger.debug(f"Met object {obj_id} is '{classification}' ({art_type}), skipping...")
                    continue

                return {
                    "title": obj.get("title", ""),
                    "artist": obj.get("artistDisplayName", ""),
                    "date": obj.get("objectDate", ""),
                    "image_url": image_url,
                }
            except Exception as e:
                logger.warning(f"Failed to fetch Met object {obj_id}: {e}")
                continue

        raise RuntimeError("Could not find a matching Met artwork after 20 attempts.")

    def _fetch_chicago_artwork(self, art_types):
        """Fetch a random artwork from the Art Institute of Chicago."""
        session = get_http_session()

        for attempt in range(20):
            page = random.randint(1, 5000)
            try:
                resp = session.get(CHICAGO_API_URL, params={
                    "page": page,
                    "limit": 1,
                    "fields": "id,title,artist_display,date_display,image_id,artwork_type_title",
                }, timeout=15)
                resp.raise_for_status()
                data = resp.json()

                artworks = data.get("data", [])
                if not artworks:
                    continue

                art = artworks[0]
                image_id = art.get("image_id")
                if not image_id:
                    logger.debug(f"Chicago artwork on page {page} has no image_id, retrying...")
                    continue

                artwork_type = art.get("artwork_type_title", "")
                art_type = self._classify_chicago(artwork_type)
                if art_type not in art_types:
                    logger.debug(f"Chicago artwork on page {page} is '{artwork_type}' ({art_type}), skipping...")
                    continue

                image_url = f"{CHICAGO_IIIF_URL}/{image_id}/full/1024,/0/default.jpg"

                return {
                    "title": art.get("title", ""),
                    "artist": art.get("artist_display", ""),
                    "date": art.get("date_display", ""),
                    "image_url": image_url,
                }
            except Exception as e:
                logger.warning(f"Failed to fetch Chicago artwork (page {page}): {e}")
                continue

        raise RuntimeError("Could not find a matching Chicago artwork after 20 attempts.")

    @staticmethod
    def _truncate_to_width(draw, text, font, max_width):
        """Binary search for the longest prefix that fits within max_width."""
        lo, hi = 10, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            bbox = draw.textbbox((0, 0), text[:mid] + "...", font=font)
            if bbox[2] - bbox[0] <= max_width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + "..."

    def _add_title_overlay(self, image, title, subtitle=""):
        """Add title and subtitle overlay at the bottom of the image."""
        if image.mode != 'RGBA':
            image = image.convert('RGBA')
        draw = ImageDraw.Draw(image, 'RGBA')
        width, height = image.size
        padding = max(10, int(height * 0.01))

        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        sub_font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

        # Title font
        title_size = max(16, int(height * 0.025))
        try:
            title_font = ImageFont.truetype(font_path, title_size)
        except Exception:
            title_font = ImageFont.load_default()

        # Subtitle font (smaller)
        sub_size = max(12, int(height * 0.018))
        try:
            sub_font = ImageFont.truetype(sub_font_path, sub_size)
        except Exception:
            sub_font = ImageFont.load_default()

        # Measure title
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_w = title_bbox[2] - title_bbox[0]
        title_h = title_bbox[3] - title_bbox[1]

        # Truncate title if too wide (binary search for optimal length)
        max_text_width = width - (padding * 4)
        display_title = title
        if title_w > max_text_width:
            display_title = self._truncate_to_width(draw, title, title_font, max_text_width)
            title_bbox = draw.textbbox((0, 0), display_title, font=title_font)
            title_w = title_bbox[2] - title_bbox[0]

        # Measure subtitle
        sub_h = 0
        display_sub = subtitle
        if subtitle:
            sub_bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
            sub_w = sub_bbox[2] - sub_bbox[0]
            sub_h = sub_bbox[3] - sub_bbox[1]
            if sub_w > max_text_width:
                display_sub = self._truncate_to_width(draw, subtitle, sub_font, max_text_width)

        # Calculate bar height
        content_height = title_h + (sub_h + 4 if subtitle else 0)
        bar_height = content_height + (padding * 2)
        bar_top = height - bar_height

        # Draw semi-transparent background
        draw.rectangle([0, bar_top, width, height], fill=(0, 0, 0, 180))

        # Draw title centered
        title_bbox = draw.textbbox((0, 0), display_title, font=title_font)
        title_w = title_bbox[2] - title_bbox[0]
        title_x = (width - title_w) // 2
        title_y = bar_top + padding

        # Outline for contrast
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if dx != 0 or dy != 0:
                    draw.text((title_x + dx, title_y + dy), display_title,
                              font=title_font, fill=(0, 0, 0, 255))
        draw.text((title_x, title_y), display_title, font=title_font, fill=(255, 255, 255, 255))

        # Draw subtitle centered below title
        if subtitle and display_sub:
            sub_bbox = draw.textbbox((0, 0), display_sub, font=sub_font)
            sub_w = sub_bbox[2] - sub_bbox[0]
            sub_x = (width - sub_w) // 2
            sub_y = title_y + title_h + 4

            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    if dx != 0 or dy != 0:
                        draw.text((sub_x + dx, sub_y + dy), display_sub,
                                  font=sub_font, fill=(0, 0, 0, 255))
            draw.text((sub_x, sub_y), display_sub, font=sub_font, fill=(200, 200, 200, 255))

        return image
