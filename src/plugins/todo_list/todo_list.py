from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw
from utils.app_utils import get_font
from utils.text_utils import get_text_dimensions, truncate_text
from utils.layout_utils import draw_rounded_rect
import logging

logger = logging.getLogger(__name__)

FONT_SIZES = {
    "x-small": 0.7,
    "small": 0.9,
    "normal": 1,
    "large": 1.1,
    "x-large": 1.3
}

BULLET_CHARS = {
    "disc": "\u2022",
    "checkbox": "\u2610",
    "checkbox-checked": "\u2611",
    "decimal": None,  # Use numbers
}

class TodoList(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        lists = []
        for title, raw_list in zip(settings['list-title[]'], settings['list[]']):
            elements = [line for line in raw_list.split('\n') if line.strip()]
            lists.append({
                'title': title,
                'elements': elements
            })

        font_scale = FONT_SIZES.get(settings.get('fontSize', 'normal'), 1)
        list_style = settings.get('listStyle', 'disc')
        main_title = settings.get('title')

        return self._render_pil(dimensions, main_title, lists, list_style, font_scale, settings)

    def _render_pil(self, dimensions, main_title, lists, list_style, font_scale, settings):
        width, height = dimensions
        bg_color = settings.get("backgroundColor", "#ffffff")
        text_color = settings.get("textColor", "#000000")

        image = Image.new("RGBA", dimensions, bg_color)
        draw = ImageDraw.Draw(image)

        margin = int(width * 0.03)
        padding = int(width * 0.02)

        # Font sizing — scale up for fewer items
        total_items = sum(len(lst['elements']) for lst in lists)
        item_scale = min(1.8, max(1.0, 8 / max(total_items, 1)))
        title_size = int(min(height * 0.075, width * 0.075) * font_scale)
        list_title_size = int(min(height * 0.055, width * 0.055) * font_scale * item_scale)
        item_size = int(min(height * 0.045, width * 0.045) * font_scale * item_scale)

        title_font = get_font("Jost", title_size, "bold")
        list_title_font = get_font("Jost", list_title_size, "bold")
        item_font = get_font("Jost", item_size)

        y = margin

        # Main title
        if main_title:
            tw = get_text_dimensions(draw, main_title, title_font)[0]
            draw.text(((width - tw) // 2, y), main_title, font=title_font, fill=text_color)
            y += int(title_size * 1.15) + int(height * 0.015)

        # Determine layout: horizontal if wide, vertical if tall
        is_horizontal = width >= height
        available_height = height - y - margin
        num_lists = len(lists)

        if is_horizontal and num_lists > 1:
            # Side by side
            gap = int(width * 0.02)
            list_width = (width - margin * 2 - gap * (num_lists - 1)) // num_lists
            for i, lst in enumerate(lists):
                lx = margin + i * (list_width + gap)
                self._draw_list(draw, lst, lx, y, list_width, available_height,
                                list_style, list_title_font, item_font,
                                text_color, padding)
        else:
            # Stacked vertically
            list_height = (available_height - int(height * 0.01) * (num_lists - 1)) // max(num_lists, 1)
            content_width = width - margin * 2
            for i, lst in enumerate(lists):
                ly = y + i * (list_height + int(height * 0.01))
                self._draw_list(draw, lst, margin, ly, content_width, list_height,
                                list_style, list_title_font, item_font,
                                text_color, padding)

        return image

    def _draw_list(self, draw, lst, x, y, w, h, list_style, title_font, item_font,
                   text_color, padding):
        # Border
        border_radius = max(4, int(w * 0.02))
        draw_rounded_rect(draw, (x, y, x + w, y + h), border_radius,
                          outline=text_color, width=2)

        inner_x = x + padding
        inner_w = w - padding * 2
        cy = y + padding

        # List title
        if lst['title']:
            draw.text((inner_x, cy), lst['title'], font=title_font, fill=text_color)
            th = get_text_dimensions(draw, lst['title'], title_font)[1]
            cy += th + int(padding * 0.5)

        # Items
        bullet = BULLET_CHARS.get(list_style, "\u2022")
        item_h = get_text_dimensions(draw, "Xg", item_font)[1]
        line_spacing = int(item_h * 0.3)
        max_y = y + h - padding

        for idx, element in enumerate(lst['elements']):
            if cy + item_h > max_y:
                remaining = len(lst['elements']) - idx
                if remaining > 0:
                    more_text = f"And {remaining} more..."
                    draw.text((inner_x, cy), more_text, font=item_font, fill=text_color)
                break

            # Bullet/number prefix
            if bullet:
                prefix = f"{bullet} "
            else:
                prefix = f"{idx + 1}. "

            prefix_w = get_text_dimensions(draw, prefix, item_font)[0]
            text = truncate_text(draw, element.strip(), item_font, inner_w - prefix_w)
            draw.text((inner_x, cy), prefix + text, font=item_font, fill=text_color)
            cy += item_h + line_spacing

            # Draw separator line after item
            draw.line((inner_x, cy, inner_x + inner_w, cy), fill=text_color, width=1)
            cy += line_spacing