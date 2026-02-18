from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw
from utils.app_utils import get_font
from utils.text_utils import get_text_dimensions, truncate_text
from utils.layout_utils import draw_rounded_rect
import os
import logging
from datetime import datetime, timedelta, timezone, date
from astral import moon
import pytz
from io import BytesIO
import math
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)
        
def get_moon_phase_name(phase_age: float) -> str:
    """Determines the name of the lunar phase based on the age of the moon."""
    PHASES_THRESHOLDS = [
        (1.0, "newmoon"),
        (7.0, "waxingcrescent"),
        (8.5, "firstquarter"),
        (14.0, "waxinggibbous"),
        (15.5, "fullmoon"),
        (22.0, "waninggibbous"),
        (23.5, "lastquarter"),
        (29.0, "waningcrescent"),
    ]

    for threshold, phase_name in PHASES_THRESHOLDS:
        if phase_age <= threshold:
            return phase_name  
    return "newmoon"

UNITS = {
    "standard": {
        "temperature": "K",
        "speed": "m/s",
        "distance":"km"
    },
    "metric": {
        "temperature": "°C",
        "speed": "m/s",
        "distance":"km"

    },
    "imperial": {
        "temperature": "°F",
        "speed": "mph",
        "distance":"mi"
    }
}

WEATHER_URL = "https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={long}&units={units}&exclude=minutely&appid={api_key}"
AIR_QUALITY_URL = "http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={long}&appid={api_key}"
GEOCODING_URL = "http://api.openweathermap.org/geo/1.0/reverse?lat={lat}&lon={long}&limit=1&appid={api_key}"

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={long}&hourly=weather_code,temperature_2m,precipitation,precipitation_probability,relative_humidity_2m,surface_pressure,visibility&daily=weathercode,temperature_2m_max,temperature_2m_min,sunrise,sunset&current=temperature,windspeed,winddirection,is_day,precipitation,weather_code,apparent_temperature&timezone=auto&models=best_match&forecast_days={forecast_days}"
OPEN_METEO_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={long}&hourly=european_aqi,uv_index,uv_index_clear_sky&timezone=auto"
OPEN_METEO_UNIT_PARAMS = {
    "standard": "temperature_unit=celsius&wind_speed_unit=ms&precipitation_unit=mm",  # temperature is converted to Kelvin later
    "metric":   "temperature_unit=celsius&wind_speed_unit=ms&precipitation_unit=mm",
    "imperial": "temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch"
}

class Weather(BasePlugin):
    """Weather dashboard plugin supporting OpenWeatherMap (One Call v3) and Open-Meteo.

    Renders current conditions (icon, temperature, feels-like, hi/lo, description),
    optional metric data points (sunrise/sunset, wind, humidity), an hourly temperature
    graph with precipitation overlay, and a multi-day forecast row with moon phases.
    Adapts layout for both horizontal (800x480) and vertical orientations.
    """

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "OpenWeatherMap",
            "expected_key": "OPEN_WEATHER_MAP_SECRET"
        }
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        # Validate and convert coordinates with proper error handling
        try:
            lat = float(settings.get('latitude'))
            long = float(settings.get('longitude'))
        except (TypeError, ValueError):
            raise RuntimeError("Latitude and Longitude must be valid numbers.")

        # Check for None/missing (0.0 is a valid coordinate)
        if settings.get('latitude') is None or settings.get('longitude') is None:
            raise RuntimeError("Latitude and Longitude are required.")

        # Validate coordinate ranges
        if not (-90 <= lat <= 90) or not (-180 <= long <= 180):
            raise RuntimeError("Invalid coordinates. Latitude must be -90 to 90, Longitude -180 to 180.")

        units = settings.get('units')
        if not units or units not in ['metric', 'imperial', 'standard']:
            raise RuntimeError("Units are required.")

        weather_provider = settings.get('weatherProvider', 'OpenWeatherMap')
        title = settings.get('customTitle', '')

        timezone = device_config.get_config("timezone", default="America/New_York")
        time_format = device_config.get_config("time_format", default="12h")
        tz = pytz.timezone(timezone)

        try:
            if weather_provider == "OpenWeatherMap":
                api_key = device_config.load_env_key("OPEN_WEATHER_MAP_SECRET")
                if not api_key:
                    raise RuntimeError("Open Weather Map API Key not configured.")
                weather_data = self.get_weather_data(api_key, units, lat, long)
                aqi_data = self.get_air_quality(api_key, lat, long)
                if settings.get('titleSelection', 'location') == 'location':
                    title = self.get_location(api_key, lat, long)
                if settings.get('weatherTimeZone', 'locationTimeZone') == 'locationTimeZone':
                    logger.info("Using location timezone for OpenWeatherMap data.")
                    wtz = self.parse_timezone(weather_data)
                    template_params = self.parse_weather_data(weather_data, aqi_data, wtz, units, time_format, lat)
                else:
                    logger.info("Using configured timezone for OpenWeatherMap data.")
                    template_params = self.parse_weather_data(weather_data, aqi_data, tz, units, time_format, lat)
            elif weather_provider == "OpenMeteo":
                forecast_days = 7
                weather_data = self.get_open_meteo_data(lat, long, units, forecast_days + 1)
                aqi_data = self.get_open_meteo_air_quality(lat, long)
                template_params = self.parse_open_meteo_data(weather_data, aqi_data, tz, units, time_format, lat)
            else:
                raise RuntimeError(f"Unknown weather provider: {weather_provider}")

            template_params['title'] = title
        except Exception as e:
            logger.error(f"{weather_provider} request failed: {str(e)}")
            raise RuntimeError(f"{weather_provider} request failure, please check logs.")
       
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        template_params["plugin_settings"] = settings

        # Add last refresh time
        now = datetime.now(tz)
        if time_format == "24h":
            last_refresh_time = now.strftime("%Y-%m-%d %H:%M")
        else:
            last_refresh_time = now.strftime("%Y-%m-%d %I:%M %p")
        template_params["last_refresh_time"] = last_refresh_time

        image = self._render_pil(dimensions, template_params, settings)

        if not image:
            raise RuntimeError("Failed to generate weather image.")
        return image

    def _render_pil(self, dimensions, data, settings):
        """Render the complete weather dashboard as a PIL Image.

        Layout (horizontal): title/date header, current conditions (icon + temp
        on left, data points on right), separator, hourly graph, forecast row.
        Vertical layout stacks sections differently. All sections are toggleable
        via plugin settings.

        Args:
            dimensions: (width, height) tuple for the output image.
            data: Parsed weather dict from parse_weather_data() or parse_open_meteo_data().
            settings: Plugin settings dict (colors, toggle flags, forecast days, etc.).

        Returns:
            PIL Image (RGBA) ready for display.
        """
        width, height = dimensions
        bg_color = settings.get("backgroundColor", "#ffffff")
        text_color = settings.get("textColor", "#000000")
        is_vertical = height > width

        image = Image.new("RGBA", dimensions, bg_color)
        draw = ImageDraw.Draw(image)

        margin = int(width * 0.02)
        show_refresh = settings.get("displayRefreshTime") == "true"
        show_metrics = settings.get("displayMetrics") == "true"
        show_graph = settings.get("displayGraph") == "true"
        show_forecast = settings.get("displayForecast") == "true"
        forecast_days = int(settings.get("forecastDays", 7))
        show_moon = settings.get("moonPhase") == "true"

        # Font sizes
        title_size = int(min(height * 0.04, width * 0.048))
        date_size = int(min(height * 0.03, width * 0.038))
        temp_size = int(min(height * 0.12, width * 0.10))
        label_size = int(min(height * 0.028, width * 0.035))
        small_size = int(min(height * 0.022, width * 0.025))
        detail_value_size = int(min(height * 0.06, width * 0.07))
        forecast_size = int(min(height * 0.032, width * 0.032))

        title_font = get_font("Jost", title_size, "bold")
        date_font = get_font("Jost", date_size)
        temp_font = get_font("Jost", temp_size, "bold")
        label_font = get_font("Jost", label_size)
        small_font = get_font("Jost", small_size)
        detail_value_font = get_font("Jost", detail_value_size, "bold")
        forecast_font = get_font("Jost", forecast_size, "bold")
        forecast_temp_font = get_font("Jost", int(forecast_size * 0.9), "bold")

        y = margin

        # Last refresh time (top right)
        if show_refresh:
            refresh_text = f"Last refresh: {data.get('last_refresh_time', '')}"
            rw = get_text_dimensions(draw, refresh_text, small_font)[0]
            draw.text((width - margin - rw, margin // 2), refresh_text, font=small_font, fill=text_color)

        # Header: Title + Date
        if data.get("title"):
            tw = get_text_dimensions(draw, data["title"], title_font)[0]
            draw.text(((width - tw) // 2, y), data["title"], font=title_font, fill=text_color)
            y += get_text_dimensions(draw, data["title"], title_font)[1] + 2

        date_text = data.get("current_date", "")
        dw = get_text_dimensions(draw, date_text, date_font)[0]
        draw.text(((width - dw) // 2, y), date_text, font=date_font, fill=text_color)
        y += get_text_dimensions(draw, date_text, date_font)[1] + 2

        # === Current conditions section ===
        current_section_h = int(height * 0.25)
        current_y = y

        if is_vertical:
            # Vertical: icon + temp centered, data points below
            icon_area_w = int(width * 0.4)
            temp_area_w = width - icon_area_w - margin * 2

            # Weather icon
            icon_path = data.get("current_day_icon", "")
            if icon_path and os.path.exists(icon_path):
                try:
                    icon_img = Image.open(icon_path).convert("RGBA")
                    icon_size = min(icon_area_w, current_section_h) - 10
                    icon_img = icon_img.resize((icon_size, icon_size), Image.LANCZOS)
                    icon_x = margin + (icon_area_w - icon_size) // 2
                    icon_y = current_y + (current_section_h - icon_size) // 2
                    image.paste(icon_img, (icon_x, icon_y), icon_img)
                except Exception:
                    pass

            # Temperature + details
            tx = margin + icon_area_w
            temp_text = data.get("current_temperature", "--")
            unit_text = data.get("temperature_unit", "")
            temp_w, temp_h = get_text_dimensions(draw, temp_text, temp_font)
            draw.text((tx, current_y), temp_text, font=temp_font, fill=text_color)
            # Unit superscript
            unit_font = get_font("Jost", int(temp_size * 0.4), "bold")
            draw.text((tx + temp_w + 2, current_y + int(temp_size * 0.15)), unit_text, font=unit_font, fill=text_color)

            detail_y = current_y + temp_h + 2
            feels_text = f"Feels Like {data.get('feels_like', '--')}"
            if data.get("units") != "standard":
                feels_text += "\u00B0"
            draw.text((tx, detail_y), feels_text, font=label_font, fill=text_color)
            detail_y += get_text_dimensions(draw, feels_text, label_font)[1] + 2

            forecast_list = data.get("forecast", [])
            if forecast_list:
                minmax = f"{forecast_list[0].get('high', '--')}\u00B0 / {forecast_list[0].get('low', '--')}\u00B0"
                draw.text((tx, detail_y), minmax, font=label_font, fill=text_color)
                detail_y += get_text_dimensions(draw, minmax, label_font)[1] + 2

            desc = data.get("weather_description", "")
            if desc:
                draw.text((tx, detail_y), desc, font=label_font, fill=text_color)

            y = current_y + current_section_h
        else:
            # Horizontal: icon+temp grouped in left 50%, data points in right 50%
            half_w = width // 2

            temp_text = data.get("current_temperature", "--")
            unit_text = data.get("temperature_unit", "")
            temp_w, temp_h = get_text_dimensions(draw, temp_text, temp_font)
            unit_font = get_font("Jost", int(temp_size * 0.4), "bold")
            unit_w = get_text_dimensions(draw, unit_text, unit_font)[0]
            temp_row_w = temp_w + 4 + unit_w

            # Build detail lines
            detail_lines = []
            feels_text = f"Feels Like {data.get('feels_like', '--')}"
            if data.get("units") != "standard":
                feels_text += "\u00B0"
            detail_lines.append(feels_text)

            forecast_list = data.get("forecast", [])
            if forecast_list:
                minmax = f"{forecast_list[0].get('high', '--')}\u00B0 / {forecast_list[0].get('low', '--')}\u00B0"
                detail_lines.append(minmax)

            desc = data.get("weather_description", "")
            if desc:
                detail_lines.append(desc)

            detail_line_h = get_text_dimensions(draw, "Xg", label_font)[1]
            detail_spacing = 2

            # --- Left 50%: Icon + temp/feels as a group, centered ---
            icon_path = data.get("current_day_icon", "")
            icon_size = min(int(half_w * 0.38), current_section_h - 10)
            icon_gap = int(width * 0.02)  # controlled gap between icon and temp

            # Total group width, centered in left half
            group_w = icon_size + icon_gap + temp_row_w
            group_x = (half_w - group_w) // 2

            if icon_path and os.path.exists(icon_path):
                try:
                    icon_img = Image.open(icon_path).convert("RGBA")
                    icon_img = icon_img.resize((icon_size, icon_size), Image.LANCZOS)
                    icon_y = current_y + (current_section_h - icon_size) // 2
                    image.paste(icon_img, (group_x, icon_y), icon_img)
                except Exception:
                    pass

            # Temp + details vertically centered
            total_detail_h = len(detail_lines) * (detail_line_h + detail_spacing)
            temp_visual_h = int(temp_size * 1.15)
            gap = int(height * 0.01)
            block_h = temp_visual_h + gap + total_detail_h
            block_y = current_y + (current_section_h - block_h) // 2

            temp_x = group_x + icon_size + icon_gap
            draw.text((temp_x, block_y), temp_text, font=temp_font, fill=text_color)
            draw.text((temp_x + temp_w + 4, block_y + int(temp_size * 0.1)), unit_text, font=unit_font, fill=text_color)

            # Detail lines centered under temp
            detail_y = block_y + temp_visual_h + gap
            temp_center_x = temp_x + temp_row_w // 2
            for line in detail_lines:
                lw = get_text_dimensions(draw, line, label_font)[0]
                draw.text((temp_center_x - lw // 2, detail_y), line, font=label_font, fill=text_color)
                detail_y += detail_line_h + detail_spacing

            # --- Right 50%: Data points ---
            if show_metrics:
                self._draw_data_points(draw, image, data.get("data_points", []),
                                       half_w, current_y, half_w, current_section_h,
                                       label_font, detail_value_font, text_color)

            y = current_y + current_section_h

        # Vertical: data points below current section
        if is_vertical and show_metrics:
            dp_h = int(height * 0.12)
            self._draw_data_points(draw, image, data.get("data_points", []),
                                   margin, y, width - margin * 2, dp_h,
                                   label_font, detail_value_font, text_color)
            y += dp_h + int(height * 0.01)

        # Separator
        draw.line((margin, y, width - margin, y), fill="#AAAAAA", width=1)
        y += int(height * 0.01)

        # Calculate forecast height first (content-driven), then graph gets the rest
        forecast_h = 0
        if show_forecast:
            # Estimate forecast tile height from content: day label + icon + temp + moon
            day_label_h = get_text_dimensions(draw, "Mon", forecast_font)[1]
            temp_label_h = get_text_dimensions(draw, "77°/57°", forecast_temp_font)[1]
            n_forecast = min(forecast_days, len(data.get("forecast", [])) - 1)
            tile_w = (width - margin * 2) // max(n_forecast, 1)
            icon_sz = min(int(tile_w * 0.70), int(height * 0.12))
            moon_h = int(tile_w * 0.18) + 12 if show_moon else 0
            tile_pad = int(tile_w * 0.05)
            forecast_h = tile_pad + day_label_h + 2 + icon_sz + 2 + temp_label_h + 14 + moon_h + tile_pad

        available_h = height - y - margin - (int(height * 0.01) if show_forecast else 0) - forecast_h

        # === Hourly temperature graph ===
        if show_graph:
            graph_h = available_h if show_graph else 0
            self._draw_hourly_graph(draw, data.get("hourly_forecast", []),
                                    margin, y, width - margin * 2, graph_h,
                                    small_font, text_color, data.get("units", "metric"))
            y += graph_h + int(height * 0.01)

        # === Forecast row ===
        if show_forecast:
            forecast_list = data.get("forecast", [])[1:forecast_days + 1]
            self._draw_forecast(draw, image, forecast_list,
                                margin, y, width - margin * 2, forecast_h,
                                forecast_font, forecast_temp_font, small_font,
                                text_color, data.get("units", "metric"), show_moon)

        return image

    def _draw_data_points(self, draw, image, data_points, x, y, w, h,
                          label_font, value_font, text_color):
        """Draw data points in a 2x2 grid matching the Chromium layout:
        icon on left, label centered above, big value centered below."""
        if not data_points:
            return
        cols = 2
        rows = (len(data_points) + cols - 1) // cols
        cell_w = w // cols
        cell_h = h // rows

        for i, dp in enumerate(data_points):
            col = i % cols
            row = i // cols
            cx = x + col * cell_w
            cy = y + row * cell_h

            label = dp.get("label", "")
            measurement = str(dp.get("measurement", ""))
            unit = dp.get("unit", "")
            arrow = dp.get("arrow", "")
            val_text = f"{measurement}{unit} {arrow}".strip()

            label_h = get_text_dimensions(draw, label, label_font)[1]
            val_h = get_text_dimensions(draw, val_text, value_font)[1]

            # Icon — large, vertically centered in left portion of cell
            icon_size = min(int(cell_w * 0.33), int(cell_h * 0.60))
            icon_path = dp.get("icon", "")
            icon_area_w = icon_size + int(cell_w * 0.02)

            if icon_path and os.path.exists(icon_path):
                try:
                    icon = Image.open(icon_path).convert("RGBA")
                    icon = icon.resize((icon_size, icon_size), Image.LANCZOS)
                    icon_y = cy + (cell_h - icon_size) // 2
                    image.paste(icon, (cx, icon_y), icon)
                except Exception:
                    pass

            # Text area — to the right of icon, centered vertically and horizontally
            text_area_x = cx + icon_area_w
            text_area_w = cell_w - icon_area_w
            text_block_h = label_h + 2 + val_h
            text_y = cy + (cell_h - text_block_h) // 2

            # Label centered
            lw = get_text_dimensions(draw, label, label_font)[0]
            draw.text((text_area_x + (text_area_w - lw) // 2, text_y),
                      label, font=label_font, fill=text_color)

            # Value centered below label
            vw = get_text_dimensions(draw, val_text, value_font)[0]
            draw.text((text_area_x + (text_area_w - vw) // 2, text_y + label_h + 2),
                      val_text, font=value_font, fill=text_color)

    def _draw_hourly_graph(self, draw, hourly, x, y, w, h,
                           font, text_color, units):
        """Draw a simple temperature line graph."""
        if not hourly or len(hourly) < 2:
            return

        temps = [hr["temperature"] for hr in hourly]
        times = [hr["time"] for hr in hourly]
        min_temp = min(temps)
        max_temp = max(temps)
        temp_range = max_temp - min_temp if max_temp != min_temp else 1

        # Graph area with padding for labels
        label_h = get_text_dimensions(draw, "12", font)[1]
        graph_x = x + int(w * 0.06)
        graph_w = w - int(w * 0.12)
        graph_y = y + label_h + 4
        graph_h = h - label_h * 2 - 8

        n = len(temps)
        points = []
        for i, temp in enumerate(temps):
            px = graph_x + int(i / (n - 1) * graph_w)
            py = graph_y + graph_h - int((temp - min_temp) / temp_range * graph_h)
            points.append((px, py))

        # Draw line
        for i in range(len(points) - 1):
            draw.line([points[i], points[i + 1]], fill="#F17A24", width=2)

        # Draw temperature labels at min/max
        degree = "\u00B0" if units != "standard" else ""
        # Y-axis: min and max
        draw.text((x, graph_y), f"{max_temp}{degree}", font=font, fill=text_color)
        draw.text((x, graph_y + graph_h - label_h), f"{min_temp}{degree}", font=font, fill=text_color)

        # X-axis time labels (every few hours)
        step = max(1, n // 6)
        for i in range(0, n, step):
            px = graph_x + int(i / (n - 1) * graph_w)
            tw = get_text_dimensions(draw, times[i], font)[0]
            draw.text((px - tw // 2, y + h - label_h), times[i], font=font, fill=text_color)

        # Precipitation bars
        precip_data = [hr.get("precipitation", 0) for hr in hourly]
        max_precip = max(precip_data) if precip_data else 0
        if max_precip > 0:
            bar_w = max(1, graph_w // n - 1)
            for i, p in enumerate(precip_data):
                if p > 0.05:
                    px = graph_x + int(i / (n - 1) * graph_w) - bar_w // 2
                    bar_h = int(p * graph_h * 0.5)
                    bar_y = graph_y + graph_h - bar_h
                    draw.rectangle((px, bar_y, px + bar_w, graph_y + graph_h),
                                   fill="#1A6FB0", outline="#1A6FB0")

    def _draw_forecast(self, draw, image, forecast, x, y, w, h,
                       day_font, temp_font, small_font, text_color, units, show_moon):
        """Draw forecast day columns."""
        if not forecast:
            return
        n = len(forecast)
        col_w = w // n
        gap = int(col_w * 0.05)
        degree = "\u00B0" if units != "standard" else ""

        for i, day in enumerate(forecast):
            cx = x + i * col_w + gap
            cw = col_w - gap * 2
            cy = y

            # Border
            draw_rounded_rect(draw, (cx, cy, cx + cw, cy + h), int(cw * 0.08),
                              outline=text_color, width=1)

            pad = int(cw * 0.05)
            iy = cy + pad

            # Day name
            day_name = day.get("day", "")
            dnw = get_text_dimensions(draw, day_name, day_font)[0]
            draw.text((cx + (cw - dnw) // 2, iy), day_name, font=day_font, fill=text_color)
            iy += get_text_dimensions(draw, day_name, day_font)[1] + 2

            # Weather icon — fill more of the tile
            icon_path = day.get("icon", "")
            icon_size = min(int(cw * 0.70), int(h * 0.45))
            if icon_path and os.path.exists(icon_path):
                try:
                    icon = Image.open(icon_path).convert("RGBA")
                    icon = icon.resize((icon_size, icon_size), Image.LANCZOS)
                    image.paste(icon, (cx + (cw - icon_size) // 2, iy), icon)
                except Exception:
                    pass
            iy += icon_size + 2

            # High / Low
            temp_text = f"{day.get('high', '--')}{degree} / {day.get('low', '--')}{degree}"
            ttw = get_text_dimensions(draw, temp_text, temp_font)[0]
            draw.text((cx + (cw - ttw) // 2, iy), temp_text, font=temp_font, fill=text_color)
            iy += get_text_dimensions(draw, temp_text, temp_font)[1] + int(h * 0.06)

            # Moon phase
            if show_moon:
                # Separator
                draw.line((cx + pad, iy, cx + cw - pad, iy), fill="#AAAAAA", width=1)
                iy += int(h * 0.04)

                moon_icon_path = day.get("moon_phase_icon", "")
                moon_size = min(int(cw * 0.2), int(h * 0.1))
                moon_pct = day.get("moon_phase_pct", "")

                moon_total_w = moon_size + 6 + get_text_dimensions(draw, f"{moon_pct} %", small_font)[0]
                moon_x = cx + (cw - moon_total_w) // 2

                if moon_icon_path and os.path.exists(moon_icon_path):
                    try:
                        moon_img = Image.open(moon_icon_path).convert("RGBA")
                        moon_img = moon_img.resize((moon_size, moon_size), Image.LANCZOS)
                        image.paste(moon_img, (moon_x, iy), moon_img)
                    except Exception:
                        pass

                draw.text((moon_x + moon_size + 6, iy + 2), f"{moon_pct} %",
                          font=small_font, fill=text_color)

    def parse_weather_data(self, weather_data, aqi_data, tz, units, time_format, lat):
        """Parse OpenWeatherMap One Call v3 response into a normalized template dict.

        Returns a dict with keys: current_date, current_day_icon, current_temperature,
        feels_like, weather_description, temperature_unit, units, time_format,
        forecast (list), data_points (list), hourly_forecast (list).
        """
        current = weather_data.get("current")
        daily_forecast = weather_data.get("daily", [])
        dt = datetime.fromtimestamp(current.get('dt'), tz=timezone.utc).astimezone(tz)
        weather_list = current.get("weather", [])
        if not weather_list:
            raise RuntimeError("Weather data missing from API response.")
        current_icon = weather_list[0].get("icon", "01d")
        icon_codes_to_preserve = ["01", "02", "10"]
        icon_code = current_icon[:2]
        current_suffix = current_icon[-1]

        if icon_code not in icon_codes_to_preserve:
            if current_icon.endswith('n'):
                current_icon = current_icon.replace("n", "d")
        # Get weather description (e.g., "Partly cloudy", "Clear sky")
        weather_description = weather_list[0].get("description", "").title()

        data = {
            "current_date": dt.strftime("%A, %B %d"),
            "current_day_icon": self.get_plugin_dir(f'icons/{current_icon}.png'),
            "current_temperature": str(round(current.get("temp"))),
            "feels_like": str(round(current.get("feels_like"))),
            "weather_description": weather_description,
            "temperature_unit": UNITS[units]["temperature"],
            "units": units,
            "time_format": time_format
        }
        data['forecast'] = self.parse_forecast(weather_data.get('daily'), tz, current_suffix, lat)
        data['data_points'] = self.parse_data_points(weather_data, aqi_data, tz, units, time_format)

        data['hourly_forecast'] = self.parse_hourly(weather_data.get('hourly'), tz, time_format, units, daily_forecast)
        return data

    def parse_open_meteo_data(self, weather_data, aqi_data, tz, units, time_format, lat):
        """Parse Open-Meteo API response into the same normalized template dict as parse_weather_data()."""
        current = weather_data.get("current", {})
        daily = weather_data.get('daily', {})
        dt = datetime.fromisoformat(current.get('time')).astimezone(tz) if current.get('time') else datetime.now(tz)
        weather_code = current.get("weather_code", 0)
        is_day = current.get("is_day", 1)
        current_icon = self.map_weather_code_to_icon(weather_code, is_day)
        
        temperature_conversion = 273.15 if units == "standard" else 0.

        # Get weather description from weather code
        weather_description = self.get_weather_description(weather_code)

        data = {
            "current_date": dt.strftime("%A, %B %d"),
            "current_day_icon": self.get_plugin_dir(f'icons/{current_icon}.png'),
            "current_temperature": str(round(current.get("temperature", 0) + temperature_conversion)),
            "feels_like": str(round(current.get("apparent_temperature", current.get("temperature", 0)) + temperature_conversion)),
            "weather_description": weather_description,
            "temperature_unit": UNITS[units]["temperature"],
            "units": units,
            "time_format": time_format
        }

        data['forecast'] = self.parse_open_meteo_forecast(weather_data.get('daily', {}), units, tz, is_day, lat)
        data['data_points'] = self.parse_open_meteo_data_points(weather_data, aqi_data, units, tz, time_format)
        
        data['hourly_forecast'] = self.parse_open_meteo_hourly(weather_data.get('hourly', {}), units, tz, time_format, daily.get('sunrise', []), daily.get('sunset', []))
        return data

    def map_weather_code_to_icon(self, weather_code, is_day):
        """Map an Open-Meteo WMO weather code to a local icon filename.

        Args:
            weather_code: WMO weather interpretation code (0-99).
            is_day: 1 for daytime, 0 for nighttime (affects clear/cloudy icons).

        Returns:
            Icon filename stem (e.g. "02d", "01n") matching files in icons/ directory.
        """
        icon = "01d" # Default to clear day icon
        
        if weather_code in [0]:   # Clear sky
            icon = "01d"
        elif weather_code in [1]: # Mainly clear
            icon = "022d"
        elif weather_code in [2]: # Partly cloudy
            icon = "02d"
        elif weather_code in [3]: # Overcast
            icon = "04d"
        elif weather_code in [51, 61, 80]: # Drizzle, showers, rain: Light
            icon = "51d"          
        elif weather_code in [53, 63, 81]: # Drizzle, showers, rain: Moderatr
            icon = "53d"
        elif weather_code in [55, 65, 82]: # Drizzle, showers, rain: Heavy
            icon = "09d"
        elif weather_code in [45]: # Fog
            icon = "50d"                       
        elif weather_code in [48]: # Icy fog
            icon = "48d"
        elif weather_code in [56, 66]: # Light freezing Drizzle
            icon = "56d"            
        elif weather_code in [57, 67]: # Freezing Drizzle
            icon = "57d"            
        elif weather_code in [71, 85]: # Snow fall: Slight
            icon = "71d"
        elif weather_code in [73]:     # Snow fall: Moderate
            icon = "73d"
        elif weather_code in [75, 86]: # Snow fall: Heavy
            icon = "13d"
        elif weather_code in [77]:     # Snow grain
            icon = "77d"
        elif weather_code in [95]: # Thunderstorm
            icon = "11d"
        elif weather_code in [96, 99]: # Thunderstorm with slight and heavy hail
            icon = "11d"

        if is_day == 0:
            if icon == "01d":
                icon = "01n"      # Clear sky night
            elif icon == "022d":
                icon = "022n"     # Mainly clear night
            elif icon == "02d":
                icon = "02n"      # Partly cloudy night                
            elif icon == "10d":
                icon = "10n"      # Rain night

        return icon

    def get_weather_description(self, weather_code):
        """Map Open-Meteo weather code to human-readable description."""
        descriptions = {
            0: "Clear Sky",
            1: "Mainly Clear",
            2: "Partly Cloudy",
            3: "Overcast",
            45: "Foggy",
            48: "Icy Fog",
            51: "Light Drizzle",
            53: "Moderate Drizzle",
            55: "Heavy Drizzle",
            56: "Light Freezing Drizzle",
            57: "Freezing Drizzle",
            61: "Light Rain",
            63: "Moderate Rain",
            65: "Heavy Rain",
            66: "Light Freezing Rain",
            67: "Freezing Rain",
            71: "Light Snow",
            73: "Moderate Snow",
            75: "Heavy Snow",
            77: "Snow Grains",
            80: "Light Showers",
            81: "Moderate Showers",
            82: "Heavy Showers",
            85: "Light Snow Showers",
            86: "Heavy Snow Showers",
            95: "Thunderstorm",
            96: "Thunderstorm with Light Hail",
            99: "Thunderstorm with Heavy Hail"
        }
        return descriptions.get(weather_code, "Unknown")

    def get_moon_phase_icon_path(self, phase_name: str, lat: float) -> str:
        """Determines the path to the moon icon, inverting it if the location is in the Southern Hemisphere."""
        # Waxing, Waning, First and Last quarter phases are inverted between hemispheres.
        if lat < 0: # Southern Hemisphere
            if phase_name == "waxingcrescent":
                phase_name = "waningcrescent"
            elif phase_name == "waxinggibbous":
                phase_name = "waninggibbous"
            elif phase_name == "waningcrescent":
                phase_name = "waxingcrescent"
            elif phase_name == "waninggibbous":
                phase_name = "waxinggibbous"
            elif phase_name == "firstquarter":
                phase_name = "lastquarter"
            elif phase_name == "lastquarter":
                phase_name = "firstquarter"
        
        return self.get_plugin_dir(f"icons/{phase_name}.png")

    def parse_forecast(self, daily_forecast, tz, current_suffix, lat):
        """
        - daily_forecast: list of daily entries from One‑Call v3 (each has 'dt', 'weather', 'temp', 'moon_phase')
        - tz: your target tzinfo (e.g. from zoneinfo or pytz)
        """
        PHASES = [
            (0.0, "newmoon"),
            (0.25, "firstquarter"),
            (0.5, "fullmoon"),
            (0.75, "lastquarter"),
            (1.0, "newmoon"),
        ]

        def choose_phase_name(phase: float) -> str:
            for target, name in PHASES:
                if math.isclose(phase, target, abs_tol=1e-3):
                    return name
            if 0.0 < phase < 0.25:
                return "waxingcrescent"
            elif 0.25 < phase < 0.5:
                return "waxinggibbous"
            elif 0.5 < phase < 0.75:
                return "waninggibbous"
            else:
                return "waningcrescent"

        forecast = []
        icon_codes_to_apply_current_suffix = ["01", "02", "10"]
        for day in daily_forecast:
            # --- weather icon ---
            weather_icon = day["weather"][0]["icon"]  # e.g. "10d", "01n"
            icon_code = weather_icon[:2]
            if icon_code in icon_codes_to_apply_current_suffix:
                weather_icon_base = weather_icon[:-1]
                weather_icon = weather_icon_base + current_suffix
            else:
                if weather_icon.endswith('n'):
                    weather_icon = weather_icon.replace("n", "d")
            weather_icon = f"{icon_code}d"        
            weather_icon_path = self.get_plugin_dir(f"icons/{weather_icon}.png")

            # --- moon phase & icon ---
            moon_phase = float(day["moon_phase"])  # [0.0–1.0]
            phase_name_north_hemi = choose_phase_name(moon_phase)
            moon_icon_path = self.get_moon_phase_icon_path(phase_name_north_hemi, lat)
            # --- true illumination percent, no decimals ---
            illum_fraction = (1 - math.cos(2 * math.pi * moon_phase)) / 2
            moon_pct = f"{illum_fraction * 100:.0f}"

            # --- date & temps ---
            dt = datetime.fromtimestamp(day["dt"], tz=timezone.utc).astimezone(tz)
            day_label = dt.strftime("%a")

            forecast.append(
                {
                    "day": day_label,
                    "high": int(day["temp"]["max"]),
                    "low": int(day["temp"]["min"]),
                    "icon": weather_icon_path,
                    "moon_phase_pct": moon_pct,
                    "moon_phase_icon": moon_icon_path,
                }
            )

        return forecast
        
    def parse_open_meteo_forecast(self, daily_data, units, tz, is_day, lat):
        """
        Parse the daily forecast from Open-Meteo API and calculate moon phase and illumination using the local 'astral' library.
        """
        times = daily_data.get('time', [])
        weather_codes = daily_data.get('weathercode', [])
        temp_max = daily_data.get('temperature_2m_max', [])
        temp_min = daily_data.get('temperature_2m_min', [])
        if units == "standard":
            temp_max = [T + 273.15 for T in temp_max]
            temp_min = [T + 273.15 for T in temp_min]

        forecast = []

        for i in range(0, len(times)): 
            dt = datetime.fromisoformat(times[i]).replace(tzinfo=timezone.utc).astimezone(tz)
            day_label = dt.strftime("%a")

            code = weather_codes[i] if i < len(weather_codes) else 0
            weather_icon = self.map_weather_code_to_icon(code, is_day=1)
            weather_icon_path = self.get_plugin_dir(f"icons/{weather_icon}.png")

            timestamp = int(dt.replace(hour=12, minute=0, second=0).timestamp())
            target_date: date = dt.date() + timedelta(days=1)

            try:
                phase_age = moon.phase(target_date)
                phase_name_north_hemi = get_moon_phase_name(phase_age)
                LUNAR_CYCLE_DAYS = 29.530588853
                phase_fraction = phase_age / LUNAR_CYCLE_DAYS
                illum_pct = (1 - math.cos(2 * math.pi * phase_fraction)) / 2 * 100
            except Exception as e:
                logger.error(f"Error calculating moon phase for {target_date}: {e}")
                illum_pct = 0
                phase_name_north_hemi = "newmoon"
            moon_icon_path = self.get_moon_phase_icon_path(phase_name_north_hemi, lat)

            forecast.append({
                "day": day_label,
                "high": int(temp_max[i]) if i < len(temp_max) else 0,
                "low": int(temp_min[i]) if i < len(temp_min) else 0,
                "icon": weather_icon_path,
                "moon_phase_pct": f"{illum_pct:.0f}",
                "moon_phase_icon": moon_icon_path
            })

        return forecast

    def parse_hourly(self, hourly_forecast, tz, time_format, units, daily_forecast):
        hourly = []
        icon_codes_to_preserve = ["01", "02", "10"]
        
        sun_map = {}
        for day in daily_forecast:
            day_date = datetime.fromtimestamp(day['dt'], tz=timezone.utc).astimezone(tz).date()
            sun_map[day_date] = (day['sunrise'], day['sunset'])
        
        for hour in hourly_forecast[:24]:
            dt_epoch = hour.get('dt')
            dt = datetime.fromtimestamp(dt_epoch, tz=timezone.utc).astimezone(tz)
            rain_mm = hour.get("rain", {}).get("1h", 0.0)
            snow_mm = hour.get("snow", {}).get("1h", 0.0)
            total_precip_mm = rain_mm + snow_mm
            sunrise, sunset = sun_map.get(dt.date(), (0, 0))
        
            is_day = sunrise <= dt_epoch < sunset
            suffix = 'd' if is_day else 'n'
        
            raw_icon = hour.get("weather", [{}])[0].get("icon", "01d")
            icon_base = raw_icon[:2]
            icon_name = f"{icon_base}{suffix}" if icon_base in icon_codes_to_preserve else f"{icon_base}d"
            
            if units == "imperial":
                precip_value = total_precip_mm / 25.4
            else:
                precip_value = total_precip_mm 
            hour_forecast = {
                "time": self.format_time(dt, time_format, hour_only=True),
                "temperature": int(hour.get("temp")),
                "precipitation": hour.get("pop"),
                "rain": round(precip_value, 2),
                "icon": self.get_plugin_dir(f'icons/{icon_name}.png')
            }
            hourly.append(hour_forecast)
        return hourly

    def parse_open_meteo_hourly(self, hourly_data, units, tz, time_format, sunrises, sunsets):
        hourly = []
        times = hourly_data.get('time', [])
        temperatures = hourly_data.get('temperature_2m', [])
        if units == "standard":
            temperatures = [temperature + 273.15 for temperature in temperatures]
        precipitation_probabilities = hourly_data.get('precipitation_probability', [])
        rain = hourly_data.get('precipitation', [])
        codes = hourly_data.get('weather_code', [])
        
        sun_map = {}
        for sr_s, ss_s in zip(sunrises, sunsets):
            sr_dt = datetime.fromisoformat(sr_s).astimezone(tz)
            ss_dt = datetime.fromisoformat(ss_s).astimezone(tz)
            sun_map[sr_dt.date()] = (sr_dt, ss_dt)
        
        current_time_in_tz = datetime.now(tz)
        start_index = 0
        for i, time_str in enumerate(times):
            try:
                dt_hourly = datetime.fromisoformat(time_str).astimezone(tz)
                if dt_hourly.date() == current_time_in_tz.date() and dt_hourly.hour >= current_time_in_tz.hour:
                    start_index = i
                    break
                if dt_hourly.date() > current_time_in_tz.date():
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} in hourly data.")
                continue

        sliced_times = times[start_index:]
        sliced_temperatures = temperatures[start_index:]
        sliced_precipitation_probabilities = precipitation_probabilities[start_index:]
        sliced_rain = rain[start_index:]
        sliced_codes = codes[start_index:]

        for i in range(min(24, len(sliced_times))):
            dt = datetime.fromisoformat(sliced_times[i]).astimezone(tz)
            sunrise, sunset = sun_map.get(dt.date(), (None, None))
            is_day = 0
            if sunrise and sunset:
                is_day = 1 if sunrise <= dt < sunset else 0
            code = sliced_codes[i] if i < len(sliced_codes) else 0
            icon_name = self.map_weather_code_to_icon(code, is_day)
            hour_forecast = {
                "time": self.format_time(dt, time_format, True),
                "temperature": int(sliced_temperatures[i]) if i < len(sliced_temperatures) else 0,
                "precipitation": (sliced_precipitation_probabilities[i] / 100) if i < len(sliced_precipitation_probabilities) else 0,
                "rain": (sliced_rain[i]) if i < len(sliced_rain) else 0,
                "icon": self.get_plugin_dir(f"icons/{icon_name}.png")
            }
            hourly.append(hour_forecast)
        return hourly

    def parse_data_points(self, weather, air_quality, tz, units, time_format):
        """Extract current metric data points (sunrise, sunset, wind, humidity) from OWM data.

        Returns a list of dicts with keys: label, measurement, unit, icon, and optional arrow.
        """
        data_points = []
        sunrise_epoch = weather.get('current', {}).get("sunrise")

        if sunrise_epoch:
            sunrise_dt = datetime.fromtimestamp(sunrise_epoch, tz=timezone.utc).astimezone(tz)
            data_points.append({
                "label": "Sunrise",
                "measurement": self.format_time(sunrise_dt, time_format, include_am_pm=False),
                "unit": "" if time_format == "24h" else sunrise_dt.strftime('%p'),
                "icon": self.get_plugin_dir('icons/sunrise.png')
            })
        else:
            logger.error(f"Sunrise not found in OpenWeatherMap response, this is expected for polar areas in midnight sun and polar night periods.")

        sunset_epoch = weather.get('current', {}).get("sunset")
        if sunset_epoch:
            sunset_dt = datetime.fromtimestamp(sunset_epoch, tz=timezone.utc).astimezone(tz)
            data_points.append({
                "label": "Sunset",
                "measurement": self.format_time(sunset_dt, time_format, include_am_pm=False),
                "unit": "" if time_format == "24h" else sunset_dt.strftime('%p'),
                "icon": self.get_plugin_dir('icons/sunset.png')
            })
        else:
            logger.error(f"Sunset not found in OpenWeatherMap response, this is expected for polar areas in midnight sun and polar night periods.")

        wind_deg = weather.get('current', {}).get("wind_deg", 0)
        wind_arrow = self.get_wind_arrow(wind_deg)
        data_points.append({
            "label": "Wind",
            "measurement": weather.get('current', {}).get("wind_speed"),
            "unit": UNITS[units]["speed"],
            "icon": self.get_plugin_dir('icons/wind.png'),
            "arrow": wind_arrow
        })

        data_points.append({
            "label": "Humidity",
            "measurement": weather.get('current', {}).get("humidity"),
            "unit": '%',
            "icon": self.get_plugin_dir('icons/humidity.png')
        })

        return data_points

    def parse_open_meteo_data_points(self, weather_data, aqi_data, units, tz, time_format):
        """Parses current data points from Open-Meteo API response."""
        data_points = []
        daily_data = weather_data.get('daily', {})
        current_data = weather_data.get('current', {})
        hourly_data = weather_data.get('hourly', {})

        current_time = datetime.now(tz)

        # Sunrise
        sunrise_times = daily_data.get('sunrise', [])
        if sunrise_times:
            sunrise_dt = datetime.fromisoformat(sunrise_times[0]).astimezone(tz)
            data_points.append({
                "label": "Sunrise",
                "measurement": self.format_time(sunrise_dt, time_format, include_am_pm=False),
                "unit": "" if time_format == "24h" else sunrise_dt.strftime('%p'),
                "icon": self.get_plugin_dir('icons/sunrise.png')
            })
        else:
            logger.error(f"Sunrise not found in Open-Meteo response, this is expected for polar areas in midnight sun and polar night periods.")

        # Sunset
        sunset_times = daily_data.get('sunset', [])
        if sunset_times:
            sunset_dt = datetime.fromisoformat(sunset_times[0]).astimezone(tz)
            data_points.append({
                "label": "Sunset",
                "measurement": self.format_time(sunset_dt, time_format, include_am_pm=False),
                "unit": "" if time_format == "24h" else sunset_dt.strftime('%p'),
                "icon": self.get_plugin_dir('icons/sunset.png')
            })
        else:
            logger.error(f"Sunset not found in Open-Meteo response, this is expected for polar areas in midnight sun and polar night periods.")

        # Wind
        wind_speed = current_data.get("windspeed", 0)
        wind_deg = current_data.get("winddirection", 0)
        wind_arrow = self.get_wind_arrow(wind_deg)
        wind_unit = UNITS[units]["speed"]
        data_points.append({
            "label": "Wind", "measurement": wind_speed, "unit": wind_unit,
            "icon": self.get_plugin_dir('icons/wind.png'), "arrow": wind_arrow
        })

        # Humidity
        current_humidity = "N/A"
        humidity_hourly_times = hourly_data.get('time', [])
        humidity_values = hourly_data.get('relative_humidity_2m', [])
        for i, time_str in enumerate(humidity_hourly_times):
            try:
                if datetime.fromisoformat(time_str).astimezone(tz).hour == current_time.hour:
                    current_humidity = int(humidity_values[i])
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} for humidity.")
                continue
        data_points.append({
            "label": "Humidity", "measurement": current_humidity, "unit": '%',
            "icon": self.get_plugin_dir('icons/humidity.png')
        })

        return data_points

    def get_wind_arrow(self, wind_deg: float) -> str:
        """Convert wind direction in degrees to a Unicode arrow character.

        The arrow points in the direction the wind is blowing *toward* (downwind),
        so a north wind (0 deg) returns "↓".
        """
        DIRECTIONS = [
            ("↓", 22.5),    # North (N)
            ("↙", 67.5),    # North-East (NE)
            ("←", 112.5),   # East (E)
            ("↖", 157.5),   # South-East (SE)
            ("↑", 202.5),   # South (S)
            ("↗", 247.5),   # South-West (SW)
            ("→", 292.5),   # West (W)
            ("↘", 337.5),   # North-West (NW)
            ("↓", 360.0)    # Wrap back to North
        ]
        wind_deg = wind_deg % 360
        for arrow, upper_bound in DIRECTIONS:
            if wind_deg < upper_bound:
                return arrow

        return "↑"

    def get_weather_data(self, api_key, units, lat, long):
        url = WEATHER_URL.format(lat=lat, long=long, units=units, api_key=api_key)
        session = get_http_session()
        response = session.get(url, timeout=30)
        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to retrieve weather data: status={response.status_code}")
            raise RuntimeError("Failed to retrieve weather data.")

        return response.json()

    def get_air_quality(self, api_key, lat, long):
        url = AIR_QUALITY_URL.format(lat=lat, long=long, api_key=api_key)
        session = get_http_session()
        response = session.get(url, timeout=30)

        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to get air quality data: status={response.status_code}")
            raise RuntimeError("Failed to retrieve air quality data.")

        return response.json()

    def get_location(self, api_key, lat, long):
        url = GEOCODING_URL.format(lat=lat, long=long, api_key=api_key)
        session = get_http_session()
        response = session.get(url, timeout=30)

        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to get location: status={response.status_code}")
            raise RuntimeError("Failed to retrieve location.")

        location_list = response.json()
        if not location_list:
            logger.warning("Geocoding returned empty results, using coordinates as location")
            return f"{lat}, {long}"
        location_data = location_list[0]
        location_str = f"{location_data.get('name')}, {location_data.get('state', location_data.get('country'))}"

        return location_str

    def get_open_meteo_data(self, lat, long, units, forecast_days):
        unit_params = OPEN_METEO_UNIT_PARAMS[units]
        url = OPEN_METEO_FORECAST_URL.format(lat=lat, long=long, forecast_days=forecast_days) + f"&{unit_params}"
        session = get_http_session()
        response = session.get(url, timeout=30)

        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to retrieve Open-Meteo weather data: status={response.status_code}")
            raise RuntimeError("Failed to retrieve Open-Meteo weather data.")

        return response.json()

    def get_open_meteo_air_quality(self, lat, long):
        url = OPEN_METEO_AIR_QUALITY_URL.format(lat=lat, long=long)
        session = get_http_session()
        response = session.get(url, timeout=30)
        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to retrieve Open-Meteo air quality data: status={response.status_code}")
            raise RuntimeError("Failed to retrieve Open-Meteo air quality data.")

        return response.json()
    
    def format_time(self, dt, time_format, hour_only=False, include_am_pm=True):
        """Format datetime based on 12h or 24h preference"""
        if time_format == "24h":
            return dt.strftime("%H:00" if hour_only else "%H:%M")
        
        if include_am_pm:
            fmt = "%I %p" if hour_only else "%I:%M %p"
        else:
            fmt = "%I" if hour_only else "%I:%M"

        return dt.strftime(fmt).lstrip("0")
    
    def parse_timezone(self, weatherdata):
        """Parse timezone from weather data"""
        if 'timezone' in weatherdata:
            logger.info(f"Using timezone from weather data: {weatherdata['timezone']}")
            return pytz.timezone(weatherdata['timezone'])
        else:
            logger.error("Failed to retrieve Timezone from weather data")
            raise RuntimeError("Timezone not found in weather data.")
