from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw
from io import BytesIO
import feedparser
import logging
import re
from utils.app_utils import get_font
from utils.text_utils import get_text_dimensions, truncate_text
from utils.http_client import get_http_session
import html

logger = logging.getLogger(__name__)

FONT_SIZES = {
    "x-small": 0.7,
    "small": 0.9,
    "normal": 1,
    "large": 1.1,
    "x-large": 1.3
}

class Rss(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        title = settings.get("title")
        feed_url = settings.get("feedUrl")
        if not feed_url:
            raise RuntimeError("RSS Feed Url is required.")

        items = self.parse_rss_feed(feed_url)

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        include_images = settings.get("includeImages") == "true"
        font_scale = FONT_SIZES.get(settings.get('fontSize', 'normal'), 1)

        return self._render_pil(dimensions, title, items[:10], include_images,
                                font_scale, settings)

    def _render_pil(self, dimensions, title, items, include_images, font_scale, settings):
        width, height = dimensions
        bg_color = settings.get("backgroundColor", "#ffffff")
        text_color = settings.get("textColor", "#000000")

        image = Image.new("RGBA", dimensions, bg_color)
        draw = ImageDraw.Draw(image)

        margin = int(width * 0.03)
        content_width = width - margin * 2

        # Font sizing
        title_size = int(min(height * 0.06, width * 0.05) * font_scale)
        item_title_size = int(min(height * 0.04, width * 0.03) * font_scale)
        desc_size = int(min(height * 0.035, width * 0.03) * font_scale)

        title_font = get_font("Jost", title_size, "bold")
        item_title_font = get_font("Jost", item_title_size, "bold")
        desc_font = get_font("Jost", desc_size)

        y = margin

        # Main title
        if title:
            tw = get_text_dimensions(draw, title, title_font)[0]
            draw.text(((width - tw) // 2, y), title, font=title_font, fill=text_color)
            y += int(title_size * 1.15) + int(height * 0.01)

        # Draw items
        item_padding = int(height * 0.02)
        img_size = int(content_width * 0.12) if include_images else 0
        max_y = height - margin

        for i, item in enumerate(items):
            if y + item_padding * 2 > max_y:
                break

            # Separator
            if i > 0:
                draw.line((margin, y, margin + content_width, y), fill=text_color, width=1)
            y += item_padding

            text_x = margin
            text_width = content_width

            # Load and draw thumbnail
            if include_images and item.get("image"):
                try:
                    thumb = self.image_loader.from_url(item["image"],
                                                       (img_size, img_size))
                    if thumb:
                        # Alternate sides
                        if i % 2 == 0:
                            img_x = margin + content_width - img_size
                            text_width = content_width - img_size - int(width * 0.01)
                        else:
                            img_x = margin
                            text_x = margin + img_size + int(width * 0.01)
                            text_width = content_width - img_size - int(width * 0.01)
                        image.paste(thumb.convert("RGBA"), (img_x, y))
                except Exception:
                    pass

            # Item title (bold, truncated)
            item_title = self._strip_html(item.get("title", ""))
            item_title = truncate_text(draw, item_title, item_title_font, text_width)
            draw.text((text_x, y), item_title, font=item_title_font, fill=text_color)
            th = get_text_dimensions(draw, item_title, item_title_font)[1]
            y += th + 2

            # Description (truncated to 2 lines)
            desc = self._strip_html(item.get("description", ""))
            if desc:
                line1 = truncate_text(draw, desc, desc_font, text_width)
                draw.text((text_x, y), line1, font=desc_font, fill=text_color)
                dh = get_text_dimensions(draw, line1, desc_font)[1]
                y += dh + 2

                # Second line if text was truncated
                if len(desc) > len(line1.rstrip(".")):
                    remaining = desc[len(line1.rstrip(".").rstrip()):]
                    line2 = truncate_text(draw, remaining.strip(), desc_font, text_width)
                    if line2 and line2 != "...":
                        draw.text((text_x, y), line2, font=desc_font, fill=text_color)
                        y += dh + 2

            y += item_padding

        return image

    def _strip_html(self, text):
        """Remove HTML tags from text."""
        clean = re.sub(r'<[^>]+>', '', text)
        return html.unescape(clean).strip()
    
    def parse_rss_feed(self, url, timeout=10):
        session = get_http_session()
        resp = session.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        
        # Parse the feed content
        feed = feedparser.parse(resp.content)
        items = []

        for entry in feed.entries:
            item = {
                "title": html.unescape(entry.get("title", "")),
                "description": html.unescape(entry.get("description", "")),
                "published": entry.get("published", ""),
                "link": entry.get("link", ""),
                "image": None
            }

            # Try to extract image from common RSS fields
            if "media_content" in entry and len(entry.media_content) > 0:
                item["image"] = entry.media_content[0].get("url")
            elif "media_thumbnail" in entry and len(entry.media_thumbnail) > 0:
                item["image"] = entry.media_thumbnail[0].get("url")
            elif "enclosures" in entry and len(entry.enclosures) > 0:
                item["image"] = entry.enclosures[0].get("url")

            items.append(item)

        return items