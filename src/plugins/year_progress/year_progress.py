from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw
from datetime import datetime, timezone
from utils.app_utils import get_font
from utils.text_utils import get_text_dimensions
from utils.layout_utils import draw_dotted_rect
import logging
import pytz

logger = logging.getLogger(__name__)
class YearProgress(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        timezone = device_config.get_config("timezone", default="America/New_York")
        tz = pytz.timezone(timezone)
        current_time = datetime.now(tz)

        start_of_year = datetime(current_time.year, 1, 1, tzinfo=tz)
        start_of_next_year = datetime(current_time.year + 1, 1, 1, tzinfo=tz)

        total_days = (start_of_next_year - start_of_year).days
        days_left = (start_of_next_year - current_time).total_seconds() / (24 * 3600)
        elapsed_days = (current_time - start_of_year).total_seconds() / (24 * 3600)

        year_percent = round((elapsed_days / total_days) * 100)

        return self._render_pil(dimensions, current_time.year, year_percent,
                                round(days_left), settings)

    def _render_pil(self, dimensions, year, year_percent, days_left, settings):
        width, height = dimensions
        bg_color = settings.get("backgroundColor", "#ffffff")
        text_color = settings.get("textColor", "#000000")

        image = Image.new("RGBA", dimensions, bg_color)
        draw = ImageDraw.Draw(image)

        margin_x = int(width * 0.05)
        content_width = width - margin_x * 2

        # Font sizes
        year_size = int(min(height * 0.20, width * 0.16))
        subtitle_size = int(min(height * 0.10, width * 0.08))
        label_size = int(min(height * 0.05, width * 0.04))

        year_font = get_font("Jost", year_size, "bold")
        subtitle_font = get_font("Jost", subtitle_size)
        label_font = get_font("Jost", label_size)

        # Vertically center the block
        bar_height = int(height * 0.10)
        year_h = int(year_size * 1.15)
        sub_h = int(subtitle_size * 1.15)
        label_h = get_text_dimensions(draw, "X", label_font)[1]
        spacing = int(height * 0.02)
        subtitle_gap = int(height * 0.08)

        total = year_h + spacing + sub_h + subtitle_gap + bar_height + spacing + label_h
        y = (height - total) // 2

        # Year title
        yw = get_text_dimensions(draw, str(year), year_font)[0]
        draw.text(((width - yw) // 2, y), str(year), font=year_font, fill=text_color)
        y += year_h + spacing

        # "PROGRESS" subtitle
        sw = get_text_dimensions(draw, "PROGRESS", subtitle_font)[0]
        draw.text(((width - sw) // 2, y), "PROGRESS", font=subtitle_font, fill=text_color)
        y += sub_h + subtitle_gap

        # Progress bar: filled portion + dotted remaining
        fill_w = int(content_width * year_percent / 100)
        bar_y = y

        # Filled part
        if fill_w > 0:
            draw.rectangle((margin_x, bar_y, margin_x + fill_w, bar_y + bar_height),
                           fill=text_color)

        # Dotted remaining part
        remaining_x = margin_x + fill_w
        if remaining_x < margin_x + content_width:
            draw_dotted_rect(draw, (remaining_x, bar_y, margin_x + content_width, bar_y + bar_height),
                             text_color, dot_spacing=5, dot_radius=1)

        y = bar_y + bar_height + spacing

        # Labels: "X% DONE" left, "X DAYS LEFT" right
        done_text = f"{year_percent}% DONE"
        left_text = f"{days_left} DAYS LEFT"
        draw.text((margin_x, y), done_text, font=label_font, fill=text_color)
        lw = get_text_dimensions(draw, left_text, label_font)[0]
        draw.text((margin_x + content_width - lw, y), left_text, font=label_font, fill=text_color)

        return image