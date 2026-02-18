from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw
from datetime import datetime, timezone
from utils.app_utils import get_font
from utils.text_utils import get_text_dimensions
import logging
import pytz

logger = logging.getLogger(__name__)
class Countdown(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        title = settings.get('title')
        countdown_date_str = settings.get('date')

        if not countdown_date_str:
            raise RuntimeError("Date is required.")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        timezone = device_config.get_config("timezone", default="America/New_York")
        tz = pytz.timezone(timezone)
        current_time = datetime.now(tz)

        countdown_date = datetime.strptime(countdown_date_str, "%Y-%m-%d")
        countdown_date = tz.localize(countdown_date)

        day_count = (countdown_date.date() - current_time.date()).days
        label = "Days Left" if day_count > 0 else "Days Passed"

        return self._render_pil(dimensions, title,
                                countdown_date.strftime("%B %d, %Y"),
                                abs(day_count), label, settings)

    def _render_pil(self, dimensions, title, date_str, day_count, label, settings):
        width, height = dimensions
        bg_color = settings.get("backgroundColor", "#ffffff")
        text_color = settings.get("textColor", "#000000")

        image = Image.new("RGBA", dimensions, bg_color)
        draw = ImageDraw.Draw(image)

        # Scale fonts relative to screen
        title_size = int(min(height * 0.11, width * 0.11))
        subtitle_size = int(min(height * 0.05, width * 0.05))
        count_size = int(min(height * 0.32, width * 0.32))
        label_size = int(min(height * 0.08, width * 0.08))

        title_font = get_font("Jost", title_size, "bold")
        subtitle_font = get_font("Jost", subtitle_size)
        count_font = get_font("Jost", count_size)
        label_font = get_font("Jost", label_size)

        # Measure all elements to vertically center the block
        elements = []
        if title:
            tw = get_text_dimensions(draw, title, title_font)[0]
            title_visual_h = int(title_size * 1.15)
            elements.append(("title", title, title_font, title_visual_h))
        elements.append(("subtitle", date_str, subtitle_font,
                         get_text_dimensions(draw, date_str, subtitle_font)[1]))
        count_str = str(day_count)
        count_visual_h = int(count_size * 1.15)
        elements.append(("count", count_str, count_font, count_visual_h))
        elements.append(("label", label.upper(), label_font,
                         get_text_dimensions(draw, label.upper(), label_font)[1]))

        # Spacing between elements
        spacing = int(height * 0.02)
        subtitle_gap = int(height * 0.04)
        total_height = sum(e[3] for e in elements) + spacing * (len(elements) - 1) + subtitle_gap

        y = (height - total_height) // 2

        for kind, text, font, h in elements:
            tw = get_text_dimensions(draw, text, font)[0]
            x = (width - tw) // 2
            draw.text((x, y), text, font=font, fill=text_color)
            y += h + spacing
            if kind == "subtitle":
                y += subtitle_gap

        return image