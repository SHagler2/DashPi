import os
import calendar as cal_mod
from utils.app_utils import resolve_path, get_font
from plugins.base_plugin.base_plugin import BasePlugin
from plugins.calendar.constants import LOCALE_MAP, FONT_SIZES
from PIL import Image, ImageColor, ImageDraw, ImageFont
from utils.text_utils import get_text_dimensions, truncate_text
from utils.layout_utils import draw_rounded_rect
import icalendar
import recurring_ical_events
from io import BytesIO
import logging
from datetime import datetime, timedelta, date
from utils.http_client import get_http_session
import pytz

logger = logging.getLogger(__name__)

class Calendar(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        template_params['locale_map'] = LOCALE_MAP
        return template_params

    def generate_image(self, settings, device_config):
        calendar_urls = settings.get('calendarURLs[]')
        calendar_colors = settings.get('calendarColors[]')
        view = settings.get("viewMode")

        if not view:
            raise RuntimeError("View is required")
        elif view not in ["timeGridDay", "timeGridWeek", "dayGrid", "dayGridMonth", "listMonth"]:
            raise RuntimeError("Invalid view")

        if not calendar_urls:
            raise RuntimeError("At least one calendar URL is required")
        for url in calendar_urls:
            if not url.strip():
                raise RuntimeError("Invalid calendar URL")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
        
        timezone = device_config.get_config("timezone", default="America/New_York")
        time_format = device_config.get_config("time_format", default="12h")
        tz = pytz.timezone(timezone)

        current_dt = datetime.now(tz)
        start, end = self.get_view_range(view, current_dt, settings)
        logger.debug(f"Fetching events for {start} --> [{current_dt}] --> {end}")
        events = self.fetch_ics_events(calendar_urls, calendar_colors, tz, start, end)
        if not events:
            logger.warning("No events found for ics url")

        if view == 'timeGridWeek' and settings.get("displayPreviousDays") != "true":
            view = 'timeGrid'

        font_scale = FONT_SIZES.get(settings.get("fontSize", "normal"), 1)

        if view in ("dayGridMonth", "dayGrid"):
            image = self._render_month_grid(dimensions, events, current_dt, tz,
                                            time_format, font_scale, settings)
        elif view == "listMonth":
            image = self._render_list(dimensions, events, current_dt, tz,
                                      time_format, font_scale, settings)
        elif view in ("timeGridDay", "timeGridWeek", "timeGrid"):
            image = self._render_time_grid(dimensions, events, current_dt, tz,
                                           time_format, font_scale, settings, view)
        else:
            raise RuntimeError(f"Unsupported view: {view}")

        if not image:
            raise RuntimeError("Failed to generate calendar image.")
        return image

    def _render_month_grid(self, dimensions, events, current_dt, tz,
                           time_format, font_scale, settings):
        width, height = dimensions
        bg_color = settings.get("backgroundColor", "#ffffff")
        text_color = settings.get("textColor", "#000000")
        show_title = settings.get("displayTitle") == "true"
        show_weekends = settings.get("displayWeekends", "true") == "true"
        week_start = int(settings.get("weekStartDay", 0))

        image = Image.new("RGBA", dimensions, bg_color)
        draw = ImageDraw.Draw(image)

        margin = int(width * 0.02)

        # Font sizes
        title_size = int(min(height * 0.055, width * 0.045) * font_scale)
        dow_size = int(min(height * 0.032, width * 0.028) * font_scale)
        day_num_size = int(min(height * 0.03, width * 0.025) * font_scale)
        event_size = int(min(height * 0.028, width * 0.023) * font_scale)

        title_font = get_font("Jost", title_size, "bold")
        dow_font = get_font("Jost", dow_size, "bold")
        day_num_font = get_font("Jost", day_num_size)
        event_font = get_font("Jost", event_size)

        y = margin

        # Title: "Month Year"
        if show_title:
            title = current_dt.strftime("%B %Y")
            tw = get_text_dimensions(draw, title, title_font)[0]
            draw.text(((width - tw) // 2, y), title, font=title_font, fill=text_color)
            y += get_text_dimensions(draw, title, title_font)[1] + 4

        # Day-of-week headers
        days_of_week = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        # Rotate based on weekStartDay
        days_of_week = days_of_week[week_start:] + days_of_week[:week_start]
        if not show_weekends:
            days_of_week = [d for d in days_of_week if d not in ("Sat", "Sun")]

        num_cols = len(days_of_week)
        col_w = (width - margin * 2) // num_cols
        dow_h = get_text_dimensions(draw, "Mon", dow_font)[1]

        for i, dow in enumerate(days_of_week):
            dx = margin + i * col_w + col_w // 2 - get_text_dimensions(draw, dow, dow_font)[0] // 2
            draw.text((dx, y), dow, font=dow_font, fill=text_color)
        y += dow_h + int(height * 0.015)
        draw.line((margin, y, width - margin, y), fill=text_color, width=1)
        y += int(height * 0.015)

        # Build calendar grid
        first_of_month = current_dt.replace(day=1)
        # Python weekday: Mon=0, Sun=6; adjust for week_start
        first_dow = first_of_month.weekday()  # Mon=0
        # Convert to display column
        py_to_display = {
            0: (1 - week_start) % 7,  # Monday
            1: (2 - week_start) % 7,  # Tuesday
            2: (3 - week_start) % 7,
            3: (4 - week_start) % 7,
            4: (5 - week_start) % 7,
            5: (6 - week_start) % 7,
            6: (0 - week_start) % 7,  # Sunday
        }
        start_col = py_to_display[first_dow]

        days_in_month = cal_mod.monthrange(current_dt.year, current_dt.month)[1]

        # Calculate rows needed
        total_cells = start_col + days_in_month
        num_rows = (total_cells + num_cols - 1) // num_cols

        available_height = height - y - margin
        row_h = available_height // num_rows

        # Parse events into a dict by date
        events_by_date = {}
        for evt in events:
            evt_start = evt["start"]
            try:
                if "T" in evt_start:
                    evt_date = datetime.fromisoformat(evt_start).date()
                else:
                    evt_date = date.fromisoformat(evt_start)
            except (ValueError, TypeError):
                continue
            events_by_date.setdefault(evt_date, []).append(evt)

            # Handle multi-day events
            if evt.get("end") and evt.get("allDay"):
                try:
                    end_date = date.fromisoformat(evt["end"])
                    d = evt_date + timedelta(days=1)
                    while d < end_date:
                        events_by_date.setdefault(d, []).append(evt)
                        d += timedelta(days=1)
                except (ValueError, TypeError):
                    pass

        today = current_dt.date()

        for day_num in range(1, days_in_month + 1):
            cell_idx = start_col + day_num - 1
            row = cell_idx // num_cols
            col = cell_idx % num_cols

            # Skip weekends if disabled
            actual_dow = days_of_week[col] if col < len(days_of_week) else ""
            if not show_weekends and actual_dow in ("Sat", "Sun"):
                continue

            cx = margin + col * col_w
            cy = y + row * row_h

            # Draw cell border
            draw.rectangle((cx, cy, cx + col_w, cy + row_h), outline=text_color, width=1)

            # Highlight today
            this_date = current_dt.replace(day=day_num).date()
            if this_date == today:
                draw.rectangle((cx + 1, cy + 1, cx + col_w - 1, cy + row_h - 1),
                               outline=text_color, width=2)

            # Day number
            num_str = str(day_num)
            draw.text((cx + 3, cy + 1), num_str, font=day_num_font, fill=text_color)
            event_y = cy + get_text_dimensions(draw, num_str, day_num_font)[1] + 3

            # Events for this day
            day_events = events_by_date.get(this_date, [])
            max_events = (cy + row_h - event_y - 2) // (get_text_dimensions(draw, "X", event_font)[1] + 2)
            max_events = max(max_events, 0)

            for ei, evt in enumerate(day_events[:max_events]):
                if ei >= max_events - 1 and len(day_events) > max_events:
                    more_text = f"+{len(day_events) - ei} more"
                    draw.text((cx + 3, event_y), more_text, font=event_font, fill=text_color)
                    break

                evt_bg = evt.get("backgroundColor", "#3788d8")
                evt_fg = evt.get("textColor", "#ffffff")
                evt_title = truncate_text(draw, evt["title"], event_font, col_w - 8)
                evt_h = get_text_dimensions(draw, evt_title, event_font)[1]

                draw_rounded_rect(draw, (cx + 2, event_y, cx + col_w - 2, event_y + evt_h + 2),
                                  2, fill=evt_bg)
                draw.text((cx + 4, event_y + 1), evt_title, font=event_font, fill=evt_fg)
                event_y += evt_h + 3

        return image

    def _render_list(self, dimensions, events, current_dt, tz,
                     time_format, font_scale, settings):
        """List month view: simple chronological list of events."""
        width, height = dimensions
        bg_color = settings.get("backgroundColor", "#ffffff")
        text_color = settings.get("textColor", "#000000")
        show_title = settings.get("displayTitle") == "true"

        image = Image.new("RGBA", dimensions, bg_color)
        draw = ImageDraw.Draw(image)

        margin = int(width * 0.03)

        title_size = int(min(height * 0.06, width * 0.05) * font_scale)
        date_size = int(min(height * 0.04, width * 0.035) * font_scale)
        event_size = int(min(height * 0.035, width * 0.03) * font_scale)

        title_font = get_font("Jost", title_size, "bold")
        date_font = get_font("Jost", date_size, "bold")
        event_font = get_font("Jost", event_size)

        y = margin

        if show_title:
            title = current_dt.strftime("%B %Y")
            tw = get_text_dimensions(draw, title, title_font)[0]
            draw.text(((width - tw) // 2, y), title, font=title_font, fill=text_color)
            y += get_text_dimensions(draw, title, title_font)[1] + int(height * 0.01)

        # Sort events by start date
        sorted_events = sorted(events, key=lambda e: e["start"])
        current_date_str = ""

        for evt in sorted_events:
            if y > height - margin:
                break

            evt_start = evt["start"]
            try:
                if "T" in evt_start:
                    evt_dt = datetime.fromisoformat(evt_start)
                    date_label = evt_dt.strftime("%A, %B %d")
                    time_label = evt_dt.strftime("%I:%M %p" if time_format == "12h" else "%H:%M").lstrip("0")
                else:
                    date_label = datetime.fromisoformat(evt_start).strftime("%A, %B %d")
                    time_label = "All Day"
            except (ValueError, TypeError):
                continue

            # New date header
            if date_label != current_date_str:
                current_date_str = date_label
                y += int(height * 0.01)
                draw.line((margin, y, width - margin, y), fill=text_color, width=1)
                y += 3
                draw.text((margin, y), date_label, font=date_font, fill=text_color)
                y += get_text_dimensions(draw, date_label, date_font)[1] + 3

            # Event entry
            evt_bg = evt.get("backgroundColor", "#3788d8")
            dot_r = 4
            draw.ellipse((margin + 2, y + 4, margin + 2 + dot_r * 2, y + 4 + dot_r * 2), fill=evt_bg)
            evt_x = margin + dot_r * 2 + 8
            evt_text = f"{time_label}  {evt['title']}"
            evt_text = truncate_text(draw, evt_text, event_font, width - evt_x - margin)
            draw.text((evt_x, y), evt_text, font=event_font, fill=text_color)
            y += get_text_dimensions(draw, evt_text, event_font)[1] + 4

        return image

    def _render_time_grid(self, dimensions, events, current_dt, tz,
                          time_format, font_scale, settings, view):
        """Time grid view: hourly slots with events."""
        width, height = dimensions
        bg_color = settings.get("backgroundColor", "#ffffff")
        text_color = settings.get("textColor", "#000000")
        show_title = settings.get("displayTitle") == "true"

        image = Image.new("RGBA", dimensions, bg_color)
        draw = ImageDraw.Draw(image)

        margin = int(width * 0.02)
        start_hour = int(settings.get("startTimeInterval", 0))
        end_hour = int(settings.get("endTimeInterval", 24))

        title_size = int(min(height * 0.05, width * 0.04) * font_scale)
        time_size = int(min(height * 0.025, width * 0.02) * font_scale)
        event_size = int(min(height * 0.025, width * 0.02) * font_scale)

        title_font = get_font("Jost", title_size, "bold")
        time_font = get_font("Jost", time_size)
        event_font = get_font("Jost", event_size)

        y = margin

        if show_title:
            if view == "timeGridDay":
                title = current_dt.strftime("%A, %B %d, %Y")
            else:
                title = current_dt.strftime("%B %Y")
            tw = get_text_dimensions(draw, title, title_font)[0]
            draw.text(((width - tw) // 2, y), title, font=title_font, fill=text_color)
            y += get_text_dimensions(draw, title, title_font)[1] + int(height * 0.01)

        # Determine number of day columns
        if view == "timeGridDay":
            num_days = 1
            start_date = current_dt.date()
        else:
            num_days = 7
            if settings.get("displayPreviousDays") == "true":
                week_start_day = int(settings.get("weekStartDay", 1))
                py_start = (week_start_day - 1) % 7
                offset = (current_dt.weekday() - py_start) % 7
                start_date = (current_dt - timedelta(days=offset)).date()
            else:
                start_date = current_dt.date()

        time_label_w = int(width * 0.08)
        grid_x = margin + time_label_w
        grid_w = width - margin * 2 - time_label_w
        col_w = grid_w // num_days if num_days > 0 else grid_w

        # Day headers for week view
        if num_days > 1:
            for d in range(num_days):
                day = start_date + timedelta(days=d)
                label = day.strftime("%a %d")
                lw = get_text_dimensions(draw, label, time_font)[0]
                dx = grid_x + d * col_w + col_w // 2 - lw // 2
                draw.text((dx, y), label, font=time_font, fill=text_color)
            y += get_text_dimensions(draw, "X", time_font)[1] + 3

        # Hour rows
        num_hours = end_hour - start_hour
        available_h = height - y - margin
        hour_h = available_h // max(num_hours, 1)

        for h in range(num_hours):
            hour = start_hour + h
            hy = y + h * hour_h

            # Hour label
            if time_format == "12h":
                label = f"{hour % 12 or 12}{'AM' if hour < 12 else 'PM'}"
            else:
                label = f"{hour:02d}:00"
            draw.text((margin, hy), label, font=time_font, fill=text_color)

            # Grid line
            draw.line((grid_x, hy, width - margin, hy), fill=text_color, width=1)

        # Draw events
        for evt in events:
            try:
                if "T" not in evt["start"]:
                    continue
                evt_start = datetime.fromisoformat(evt["start"])
                evt_date = evt_start.date()
                day_offset = (evt_date - start_date).days
                if day_offset < 0 or day_offset >= num_days:
                    continue

                start_minutes = evt_start.hour * 60 + evt_start.minute - start_hour * 60
                if start_minutes < 0:
                    start_minutes = 0

                evt_end = None
                if evt.get("end"):
                    evt_end = datetime.fromisoformat(evt["end"])
                duration_min = (evt_end - evt_start).total_seconds() / 60 if evt_end else 60

                px_per_min = hour_h / 60
                ey = y + int(start_minutes * px_per_min)
                eh = max(int(duration_min * px_per_min), get_text_dimensions(draw, "X", event_font)[1] + 4)
                ex = grid_x + day_offset * col_w + 1
                ew = col_w - 2

                evt_bg = evt.get("backgroundColor", "#3788d8")
                evt_fg = evt.get("textColor", "#ffffff")

                draw_rounded_rect(draw, (ex, ey, ex + ew, ey + eh), 2, fill=evt_bg)
                evt_title = truncate_text(draw, evt["title"], event_font, ew - 4)
                draw.text((ex + 2, ey + 1), evt_title, font=event_font, fill=evt_fg)

            except (ValueError, TypeError, KeyError):
                continue

        return image
    
    def fetch_ics_events(self, calendar_urls, colors, tz, start_range, end_range):
        parsed_events = []

        for calendar_url, color in zip(calendar_urls, colors):
            cal = self.fetch_calendar(calendar_url)
            events = recurring_ical_events.of(cal).between(start_range, end_range)
            contrast_color = self.get_contrast_color(color)

            for event in events:
                start, end, all_day = self.parse_data_points(event, tz)
                parsed_event = {
                    "title": str(event.get("summary")),
                    "start": start,
                    "backgroundColor": color,
                    "textColor": contrast_color,
                    "allDay": all_day
                }
                if end:
                    parsed_event['end'] = end

                parsed_events.append(parsed_event)

        return parsed_events
    
    def get_view_range(self, view, current_dt, settings):
        start = datetime(current_dt.year, current_dt.month, current_dt.day)
        if view == "timeGridDay":
            end = start + timedelta(days=1)
        elif view == "timeGridWeek":
            if settings.get("displayPreviousDays") == "true":
                week_start_day = int(settings.get("weekStartDay", 1))
                python_week_start = (week_start_day - 1) % 7
                offset = (current_dt.weekday() - python_week_start) % 7
                start = current_dt - timedelta(days=offset)
                start = datetime(start.year, start.month, start.day)
            end = start + timedelta(days=7)
        elif view == "dayGrid":
            start = current_dt - timedelta(weeks=1)
            end = current_dt + timedelta(weeks=int(settings.get("displayWeeks") or 4))
        elif view == "dayGridMonth":
            start = datetime(current_dt.year, current_dt.month, 1) - timedelta(weeks=1)
            end = datetime(current_dt.year, current_dt.month, 1) + timedelta(weeks=6)
        elif view == "listMonth":
            end = start + timedelta(weeks=5)
        return start, end
        
    def parse_data_points(self, event, tz):
        all_day = False
        dtstart = event.decoded("dtstart")
        if isinstance(dtstart, datetime):
            start = dtstart.astimezone(tz).isoformat()
        else:
            start = dtstart.isoformat()
            all_day = True

        end = None
        if "dtend" in event:
            dtend = event.decoded("dtend")
            if isinstance(dtend, datetime):
                end = dtend.astimezone(tz).isoformat()
            else:
                end = dtend.isoformat()
        elif "duration" in event:
            duration = event.decoded("duration")
            end = (dtstart + duration).isoformat()
        return start, end, all_day

    def fetch_calendar(self, calendar_url):
        # workaround for webcal urls
        if calendar_url.startswith("webcal://"):
            calendar_url = calendar_url.replace("webcal://", "https://")
        try:
            session = get_http_session()
            response = session.get(calendar_url, timeout=30)
            response.raise_for_status()
            return icalendar.Calendar.from_ical(response.text)
        except Exception as e:
            raise RuntimeError(f"Failed to fetch iCalendar url: {str(e)}")

    def get_contrast_color(self, color):
        """
        Returns '#000000' (black) or '#ffffff' (white) depending on the contrast
        against the given color.
        """
        r, g, b = ImageColor.getrgb(color)
        # YIQ formula to estimate brightness
        yiq = (r * 299 + g * 587 + b * 114) / 1000

        return '#000000' if yiq >= 150 else '#ffffff'
