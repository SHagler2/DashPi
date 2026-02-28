import json
import logging
import math
import os
import threading
import time as time_module
from datetime import datetime, timezone, timedelta

from PIL import Image, ImageDraw, ImageFont
from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import get_font, resolve_path
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"
CREW_URL = "http://api.open-notify.org/astros.json"
TLE_CACHE_MAX_AGE = 6 * 3600  # 6 hours
EARTH_RADIUS_KM = 6371.0
ISS_CATALOG_NUMBER = 25544

# Mode trigger thresholds
PREPASS_TRIGGER_DEFAULT = 20  # minutes before pass
POSTPASS_DURATION = 5  # minutes after pass

# Cache refresh intervals (seconds)
PASS_REFRESH_INTERVAL = 300    # 5 minutes
CREW_REFRESH_INTERVAL = 1800   # 30 minutes
TRACK_REFRESH_INTERVAL = 30    # 30 seconds
GEOCODE_MOVE_THRESHOLD = 0.5   # degrees before re-geocoding
VIEWPORT_MOVE_THRESHOLD = 1.0  # degrees before re-cropping map


class ISSTracker(BasePlugin):
    def __init__(self, config, **dependencies):
        super().__init__(config, **dependencies)
        self._lock = threading.Lock()

        # Cached heavy data
        self._cached_passes = None
        self._last_pass_fetch_time = 0

        self._cached_crew_count = 0
        self._last_crew_fetch_time = 0

        # Cached map viewport
        self._cached_viewport = None
        self._viewport_key = None

        # Cached ground track points
        self._cached_ground_track = None
        self._last_track_time = 0

        # Cached pass arc (expensive: loads de421.bsp)
        self._cached_pass_arc = None
        self._pass_arc_key = None

        # Cached reverse geocode
        self._cached_over_text = None
        self._over_text_position = None

        # Loaded-once resources
        self._world_map = None
        self._landmarks = None
        self._iss_marker = None

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = False
        template_params['hide_refresh_interval'] = True
        template_params['api_key'] = {
            "required": False,
            "service": "N2YO",
            "expected_key": "N2YO_SECRET"
        }
        return template_params

    def _get_world_map(self):
        """Load world map image once and cache in memory."""
        if self._world_map is None:
            map_path = os.path.join(self.get_plugin_dir(), "resources", "world_map.png")
            try:
                self._world_map = Image.open(map_path).convert("RGB")
                logger.info("World map loaded into memory")
            except Exception:
                logger.warning("World map not found")
                self._world_map = False  # sentinel to avoid retrying
        return self._world_map if self._world_map is not False else None

    def _get_landmarks(self):
        """Load landmarks.json once and cache in memory."""
        if self._landmarks is None:
            landmarks_path = os.path.join(self.get_plugin_dir(), "resources", "landmarks.json")
            try:
                with open(landmarks_path, "r") as f:
                    self._landmarks = json.load(f)
                logger.info(f"Loaded {len(self._landmarks)} landmarks")
            except Exception:
                self._landmarks = []
        return self._landmarks

    def _get_iss_marker(self, map_dimension):
        """Load and scale the ISS marker image, cached after first load."""
        if self._iss_marker is None:
            marker_path = os.path.join(self.get_plugin_dir(), "resources", "iss_marker.png")
            try:
                self._iss_marker = Image.open(marker_path).convert("RGBA")
                logger.info("ISS marker image loaded")
            except Exception:
                logger.warning("ISS marker image not found")
                self._iss_marker = False
        marker = self._iss_marker if self._iss_marker is not False else None
        if marker:
            # Scale to ~15% of map dimension
            target = max(40, int(map_dimension * 0.15))
            ratio = target / max(marker.width, marker.height)
            scaled = marker.resize(
                (int(marker.width * ratio), int(marker.height * ratio)),
                Image.LANCZOS,
            )
            # Tint to red for contrast against ocean blue and land green
            import numpy as np
            arr = np.array(scaled, dtype=np.float32)
            lum = 0.299 * arr[:,:,0] + 0.587 * arr[:,:,1] + 0.114 * arr[:,:,2]
            lum = lum / 255.0
            arr[:,:,0] = lum * 255  # R channel
            arr[:,:,1] = lum * 50   # G channel
            arr[:,:,2] = lum * 30   # B channel
            # Keep original alpha
            tinted = Image.fromarray(arr.astype(np.uint8), "RGBA")
            return tinted
        return None

    def _get_pass_arc(self, tle_lines, pass_data, obs_lat, obs_lon):
        """Get pass arc, using cache if available for this pass."""
        if not pass_data or "rise_utc" not in pass_data:
            return []
        key = pass_data["rise_utc"].isoformat()
        if self._cached_pass_arc is not None and self._pass_arc_key == key:
            return self._cached_pass_arc
        arc = _compute_pass_arc(tle_lines, pass_data, obs_lat, obs_lon)
        self._cached_pass_arc = arc
        self._pass_arc_key = key
        logger.info(f"Computed pass arc: {len(arc)} points")
        return arc

    def generate_image(self, settings, device_config):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        lat = _parse_float(settings.get("latitude"), None)
        lon = _parse_float(settings.get("longitude"), None)

        # Fall back to weather plugin location if not configured
        weather_city = ""
        if lat is None or lon is None:
            w_lat, w_lon, weather_city = _find_weather_location(device_config)
            if lat is None:
                lat = w_lat
            if lon is None:
                lon = w_lon

        units = settings.get("units", "metric")
        prepass_minutes = _parse_int(
            settings.get("prepassTrigger"), PREPASS_TRIGGER_DEFAULT
        )

        tle_cache_path = os.path.join(self.get_plugin_dir(), "iss_tle_cache.json")
        tle_lines = _load_tle(tle_cache_path)

        now_utc = datetime.now(timezone.utc)
        now_mono = time_module.monotonic()

        # TIER 1: Always compute (cheap SGP4 math)
        iss_lat, iss_lon, iss_alt_km = _compute_iss_position(tle_lines, now_utc)
        speed_kmh = _orbital_speed(iss_alt_km)

        with self._lock:
            # TIER 2: Pass predictions — refresh every 5 minutes or when all cached passes are stale
            all_stale = (self._cached_passes is not None and
                         all(p.get("set_utc", now_utc) <= now_utc for p in self._cached_passes))
            if self._cached_passes is None or all_stale or (now_mono - self._last_pass_fetch_time) >= PASS_REFRESH_INTERVAL:
                n2yo_api_key = device_config.load_env_key("N2YO_SECRET")
                try:
                    new_passes = _predict_passes(tle_lines, lat, lon, now_utc, n2yo_api_key)
                    if new_passes is not None:
                        self._cached_passes = new_passes
                        self._last_pass_fetch_time = now_mono
                        logger.info(f"Refreshed pass predictions: {len(new_passes)} passes")
                except Exception as e:
                    logger.warning(f"Pass prediction failed: {e}")
            # Filter out passes that have already ended
            all_passes = self._cached_passes or []
            passes = [p for p in all_passes if p.get("set_utc", now_utc) > now_utc]

            # TIER 3: Crew count — refresh every 30 minutes
            if self._cached_crew_count == 0 or (now_mono - self._last_crew_fetch_time) >= CREW_REFRESH_INTERVAL:
                count = _get_crew_count()
                if count > 0:
                    self._cached_crew_count = count
                    self._last_crew_fetch_time = now_mono
            crew_count = self._cached_crew_count

            # TIER 4: Reverse geocode — only when ISS moves significantly
            landmarks = self._get_landmarks()
            if (self._cached_over_text is None or self._over_text_position is None or
                    abs(iss_lat - self._over_text_position[0]) > GEOCODE_MOVE_THRESHOLD or
                    abs(iss_lon - self._over_text_position[1]) > GEOCODE_MOVE_THRESHOLD):
                self._cached_over_text = _reverse_geocode_from_data(iss_lat, iss_lon, landmarks)
                self._over_text_position = (iss_lat, iss_lon)
            over_text = self._cached_over_text

            # TIER 5: Ground track — refresh every 30 seconds
            if self._cached_ground_track is None or (now_mono - self._last_track_time) >= TRACK_REFRESH_INTERVAL:
                self._cached_ground_track = _compute_ground_track(tle_lines, now_utc)
                self._last_track_time = now_mono

        mode = _determine_mode(now_utc, passes, prepass_minutes)

        timezone_name = device_config.get_config("timezone", default="UTC")
        time_format = device_config.get_config("time_format", default="12h")

        obs_city = settings.get("cityName", "").split(",")[0].strip()
        if not obs_city and weather_city:
            obs_city = weather_city.split(",")[0].strip()
        if not obs_city:
            obs_city = _nearest_city_from_data(lat, lon, landmarks)

        if mode == "nadir":
            img = self._render_nadir(
                dimensions, iss_lat, iss_lon, iss_alt_km, speed_kmh,
                crew_count, over_text, passes, units, timezone_name,
                time_format, now_utc, obs_city,
            )
        elif mode == "prepass":
            active_pass = _get_active_pass(now_utc, passes, prepass_minutes)
            arc_points = self._get_pass_arc(tle_lines, active_pass, lat, lon)
            img = self._render_skyplot(
                dimensions, active_pass, arc_points, now_utc,
                timezone_name, time_format,
                during_pass=_is_during_pass(now_utc, active_pass),
            )
        else:  # postpass
            recent_pass = _get_recent_pass(now_utc, passes)
            arc_points = self._get_pass_arc(tle_lines, recent_pass, lat, lon)
            img = self._render_postpass(
                dimensions, recent_pass, arc_points,
                now_utc, timezone_name, time_format,
            )

        return img

    # ───────── Nadir View ─────────

    def _render_nadir(
        self,
        dimensions,
        iss_lat,
        iss_lon,
        alt_km,
        speed_kmh,
        crew_count,
        over_text,
        passes,
        units,
        timezone_name,
        time_format,
        now_utc,
        obs_city="",
    ):
        w, h = dimensions
        info_h = int(h * 0.18)
        map_h = h - info_h

        world = self._get_world_map()
        map_img = self._crop_map_viewport(world, iss_lat, iss_lon, w, map_h)

        img = Image.new("RGBA", dimensions, (15, 20, 30, 255))
        img.paste(map_img, (0, 0))

        # Dim the map slightly to reduce glare and improve marker visibility
        dim_overlay = Image.new("RGBA", (w, map_h), (0, 0, 0, 70))
        img.alpha_composite(dim_overlay, (0, 0))

        draw = ImageDraw.Draw(img)

        # Draw ground footprint circle
        footprint_radius_deg = _footprint_radius(alt_km)
        self._draw_footprint(draw, iss_lat, iss_lon, footprint_radius_deg, w, map_h)

        # Draw ground track from cache
        self._draw_ground_track(draw, self._cached_ground_track or [], iss_lat, iss_lon, w, map_h)

        # Draw ISS marker image at center
        cx, cy = w // 2, map_h // 2
        marker = self._get_iss_marker(min(w, map_h))
        if marker:
            mx, my = cx - marker.width // 2, cy - marker.height // 2
            img.paste(marker, (mx, my), marker)

        # Info strip
        self._draw_info_strip(
            draw, w, h, info_h, map_h, iss_lat, iss_lon, alt_km,
            speed_kmh, crew_count, over_text, passes, units,
            timezone_name, time_format, now_utc, obs_city,
        )

        return img.convert("RGB")

    def _crop_map_viewport(self, world, lat, lon, vw, vh):
        """Crop viewport from pre-loaded world map with caching."""
        # Check cache: re-crop only when ISS moves >1 degree
        viewport_key = (round(lat, 0), round(lon, 0), vw, vh)
        if self._cached_viewport is not None and self._viewport_key == viewport_key:
            return self._cached_viewport

        if world is None:
            return Image.new("RGB", (vw, vh), (30, 40, 50))

        mw, mh = world.size

        # Convert lat/lon to pixel on equirectangular map
        px = (lon + 180) / 360 * mw
        py = (90 - lat) / 180 * mh

        # Scale factor: viewport pixels per map pixel
        scale_x = vw / mw
        scale_y = vh / mh
        scale = max(scale_x, scale_y) * 3.0  # zoom level ~3x

        crop_w = int(vw / scale)
        crop_h = int(vh / scale)

        x1 = int(px - crop_w // 2)
        y1 = int(py - crop_h // 2)

        # Handle vertical bounds (clamp)
        if y1 < 0:
            y1 = 0
        if y1 + crop_h > mh:
            y1 = mh - crop_h

        # Handle horizontal wrapping at dateline
        if x1 < 0 or x1 + crop_w > mw:
            viewport = Image.new("RGB", (crop_w, crop_h), (30, 40, 50))
            if x1 < 0:
                right_part = world.crop((mw + x1, y1, mw, y1 + crop_h))
                left_part = world.crop((0, y1, x1 + crop_w, y1 + crop_h))
                viewport.paste(right_part, (0, 0))
                viewport.paste(left_part, (right_part.width, 0))
            else:
                left_part = world.crop((x1, y1, mw, y1 + crop_h))
                right_part = world.crop((0, y1, crop_w - left_part.width, y1 + crop_h))
                viewport.paste(left_part, (0, 0))
                viewport.paste(right_part, (left_part.width, 0))
        else:
            viewport = world.crop((x1, y1, x1 + crop_w, y1 + crop_h))

        result = viewport.resize((vw, vh), Image.LANCZOS)
        self._cached_viewport = result
        self._viewport_key = viewport_key
        logger.info(f"Re-cropped map viewport at ({lat:.0f}, {lon:.0f})")
        return result

    def _draw_footprint(self, draw, lat, lon, radius_deg, w, map_h):
        # Draw a circle on the map representing the ISS footprint
        # The circle is centered on the viewport (since map is centered on ISS)
        cx, cy = w // 2, map_h // 2

        # Approximate pixel radius based on viewport scale
        # The viewport shows roughly 120 degrees of longitude
        degrees_visible_lon = 120
        px_per_deg = w / degrees_visible_lon
        r = int(radius_deg * px_per_deg)

        if r > 5:
            points = []
            for angle in range(0, 361, 5):
                rad = math.radians(angle)
                x = cx + r * math.cos(rad)
                y = cy + r * math.sin(rad)
                points.append((x, y))
            if len(points) > 2:
                draw.line(points, fill=(0, 180, 0), width=2)

    def _draw_ground_track(self, draw, track_points, ref_lat, ref_lon, w, map_h):
        """Draw pre-computed ground track points on the map."""
        if len(track_points) < 2:
            return

        # Convert to viewport pixels
        degrees_visible_lon = 120
        degrees_visible_lat = degrees_visible_lon * map_h / w
        px_per_deg_x = w / degrees_visible_lon
        px_per_deg_y = map_h / degrees_visible_lat

        cx, cy = w // 2, map_h // 2
        pixel_points = []
        prev_px = None
        for lat, lon in track_points:
            dlon = lon - ref_lon
            if dlon > 180:
                dlon -= 360
            elif dlon < -180:
                dlon += 360
            dlat = lat - ref_lat

            px = cx + dlon * px_per_deg_x
            py = cy - dlat * px_per_deg_y

            if prev_px is not None and abs(px - prev_px) > w * 0.5:
                if len(pixel_points) > 1:
                    draw.line(pixel_points, fill=(255, 200, 0), width=1)
                pixel_points = []

            pixel_points.append((px, py))
            prev_px = px

        if len(pixel_points) > 1:
            draw.line(pixel_points, fill=(255, 200, 0), width=1)

    def _draw_info_strip(
        self,
        draw,
        w,
        h,
        info_h,
        map_h,
        lat,
        lon,
        alt_km,
        speed_kmh,
        crew_count,
        over_text,
        passes,
        units,
        timezone_name,
        time_format,
        now_utc,
        obs_city="",
    ):
        # Background
        draw.rectangle([(0, map_h), (w, h)], fill=(0, 0, 0))

        is_vertical = h > w

        font_size = max(int(info_h * 0.22), 12)
        small_font_size = max(int(info_h * 0.18), 10)
        font = get_font("Jost", font_size)
        small_font = get_font("Jost", small_font_size)

        text_color = (255, 255, 255)
        accent_color = (100, 200, 100)

        padding = int(w * 0.02)
        y_base = map_h + int(info_h * 0.1)
        line_spacing = int(font_size * 1.4)

        # Line 1: Over text
        draw.text(
            (padding, y_base),
            f"Over: {over_text}",
            fill=accent_color,
            font=font,
        )

        # Vertical mode: simplified info strip (full details in horizontal only)
        if is_vertical:
            if units == "imperial":
                alt_str = f"Alt: {alt_km * 0.621371:.0f} mi"
            else:
                alt_str = f"Alt: {alt_km:.0f} km"
            lat_dir = "N" if lat >= 0 else "S"
            lon_dir = "E" if lon >= 0 else "W"
            coord_str = f"{abs(lat):.1f}\u00b0{lat_dir}, {abs(lon):.1f}\u00b0{lon_dir}"
            draw.text(
                (padding, y_base + line_spacing),
                f"{alt_str}  |  {coord_str}",
                fill=text_color,
                font=small_font,
            )
            hint = "Pass details available in horizontal mode"
            hint_w = draw.textbbox((0, 0), hint, font=small_font)[2]
            draw.text(
                ((w - hint_w) // 2, y_base + line_spacing * 2),
                hint,
                fill=(120, 120, 120),
                font=small_font,
            )
            return

        # Line 2: Alt, Speed, Crew
        if units == "imperial":
            alt_str = f"Alt: {alt_km * 0.621371:.0f} mi"
            speed_str = f"Speed: {speed_kmh * 0.621371:.0f} mph"
        else:
            alt_str = f"Alt: {alt_km:.0f} km"
            speed_str = f"Speed: {speed_kmh:.0f} km/h"
        crew_str = f"Crew: {crew_count}" if crew_count > 0 else ""

        info_line = f"{alt_str}  |  {speed_str}"
        if crew_str:
            info_line += f"  |  {crew_str}"
        draw.text(
            (padding, y_base + line_spacing),
            info_line,
            fill=text_color,
            font=small_font,
        )

        # Line 3: Coordinates
        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"
        coord_str = f"{abs(lat):.1f}\u00b0{lat_dir}, {abs(lon):.1f}\u00b0{lon_dir}"
        draw.text(
            (padding, y_base + line_spacing * 2),
            coord_str,
            fill=(180, 180, 180),
            font=small_font,
        )

        # Right side: Next pass + next visible pass info
        try:
            import pytz
            tz = pytz.timezone(timezone_name)
        except Exception:
            tz = timezone.utc

        next_any = passes[0] if passes else None
        next_visible = next((p for p in passes if p.get("visible")), None)

        def _format_pass_time(p):
            rise_local = p["rise_utc"].astimezone(tz)
            now_local = now_utc.astimezone(tz)
            time_str = rise_local.strftime("%H:%M") if time_format == "24h" else rise_local.strftime("%I:%M %p").lstrip("0")
            if rise_local.date() == now_local.date():
                return f"Today {time_str}"
            elif rise_local.date() == (now_local + timedelta(days=1)).date():
                return f"Tomorrow {time_str}"
            else:
                return f"{rise_local.strftime('%b %-d')} {time_str}"

        def _right_align(text, y, fill, f):
            bbox = draw.textbbox((0, 0), text, font=f)
            draw.text((w - padding - (bbox[2] - bbox[0]), y), text, fill=fill, font=f)

        # Tier 1 (headline/accent): Visible pass — the key info
        # Tier 2 (detail/white): Next overhead pass
        # Tier 3 (meta/gray): Direction and duration
        meta_color = (180, 180, 180)
        line = 0

        if next_visible:
            vis_time = _format_pass_time(next_visible)
            vis_el = next_visible.get("max_elevation", 0)
            if obs_city:
                vis_text = f"Visible over {obs_city}: {vis_time} ({vis_el:.0f}\u00b0)"
            else:
                vis_text = f"Next visible: {vis_time} ({vis_el:.0f}\u00b0)"
            _right_align(vis_text, y_base + line_spacing * line, accent_color, font)
            line += 1

            # Next overhead pass (if different from visible)
            if next_any and next_any is not next_visible:
                any_time = _format_pass_time(next_any)
                any_el = next_any.get("max_elevation", 0)
                _right_align(f"Next pass: {any_time} ({any_el:.0f}\u00b0)",
                             y_base + line_spacing * line, text_color, small_font)
                line += 1

            # Direction and duration for visible pass
            rise_az = next_visible.get("rise_azimuth")
            set_az = next_visible.get("set_azimuth")
            if rise_az is not None and set_az is not None and "set_utc" in next_visible:
                rise_dir = _azimuth_to_compass(rise_az)
                set_dir = _azimuth_to_compass(set_az)
                duration_s = (next_visible["set_utc"] - next_visible["rise_utc"]).total_seconds()
                duration_min = int(duration_s // 60)
                _right_align(f"Look {rise_dir} \u2192 {set_dir}, {duration_min} min",
                             y_base + line_spacing * line, meta_color, small_font)
        elif next_any:
            # No visible passes — show next overhead as headline
            any_time = _format_pass_time(next_any)
            any_el = next_any.get("max_elevation", 0)
            _right_align(f"Next pass: {any_time} ({any_el:.0f}\u00b0)", y_base, accent_color, font)
            _right_align("No visible passes upcoming",
                         y_base + line_spacing, meta_color, small_font)

    # ───────── Sky Plot (Pre-pass / During Pass) ─────────

    def _render_skyplot(
        self,
        dimensions,
        pass_data,
        arc_points,
        now_utc,
        timezone_name,
        time_format,
        during_pass=False,
    ):
        w, h = dimensions
        img = Image.new("RGB", dimensions, (0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Sky plot on left 60% of display
        plot_size = min(int(w * 0.55), h - 40)
        plot_cx = int(w * 0.3)
        plot_cy = h // 2
        plot_r = plot_size // 2 - 10

        self._draw_polar_grid(draw, plot_cx, plot_cy, plot_r)

        if pass_data and arc_points:
            self._draw_pass_arc(
                draw, arc_points, plot_cx, plot_cy, plot_r, now_utc, during_pass
            )

            # Info panel on right
            self._draw_pass_info_panel(
                draw, w, h, pass_data, now_utc,
                timezone_name, time_format, during_pass,
            )

        return img

    def _draw_polar_grid(self, draw, cx, cy, radius):
        # Elevation rings at 0, 30, 60, 90 degrees
        for el in [0, 30, 60, 90]:
            r = int(radius * (90 - el) / 90)
            if r > 0:
                draw.ellipse(
                    [cx - r, cy - r, cx + r, cy + r],
                    outline=(60, 60, 60),
                    width=1,
                )
            # Label
            if el > 0 and el < 90:
                font = get_font("Jost", max(int(radius * 0.08), 10))
                draw.text(
                    (cx + 3, cy - r + 2),
                    f"{el}\u00b0",
                    fill=(100, 100, 100),
                    font=font,
                )

        # Cardinal directions
        font = get_font("Jost", max(int(radius * 0.12), 12))
        cardinals = [("N", 0), ("E", 90), ("S", 180), ("W", 270)]
        for label, az in cardinals:
            angle_rad = math.radians(az - 90)  # N=up
            lx = cx + int((radius + 15) * math.cos(angle_rad))
            ly = cy + int((radius + 15) * math.sin(angle_rad))
            draw.text((lx, ly), label, fill=(180, 180, 180), font=font, anchor="mm")

        # Cross lines
        draw.line([(cx - radius, cy), (cx + radius, cy)], fill=(40, 40, 40), width=1)
        draw.line([(cx, cy - radius), (cx, cy + radius)], fill=(40, 40, 40), width=1)

    def _draw_pass_arc(
        self, draw, arc_points, cx, cy, radius, now_utc, during_pass
    ):
        if not arc_points:
            return

        for i in range(len(arc_points) - 1):
            t1, az1, el1, sunlit1 = arc_points[i]
            t2, az2, el2, sunlit2 = arc_points[i + 1]

            x1, y1 = _azel_to_xy(az1, el1, cx, cy, radius)
            x2, y2 = _azel_to_xy(az2, el2, cx, cy, radius)

            if during_pass and t1 <= now_utc:
                color = (200, 200, 0)  # traversed = yellow
            elif sunlit1:
                color = (0, 220, 0)  # sunlit/visible = green
            else:
                color = (80, 80, 80)  # shadow = dim

            draw.line([(x1, y1), (x2, y2)], fill=color, width=3)

        # Start marker (green dot)
        sx, sy = _azel_to_xy(
            arc_points[0][1], arc_points[0][2], cx, cy, radius
        )
        draw.ellipse([sx - 5, sy - 5, sx + 5, sy + 5], fill=(0, 200, 0))

        # End marker (red dot)
        ex, ey = _azel_to_xy(
            arc_points[-1][1], arc_points[-1][2], cx, cy, radius
        )
        draw.ellipse([ex - 5, ey - 5, ex + 5, ey + 5], fill=(200, 0, 0))

        # During pass: current position marker
        if during_pass:
            for i in range(len(arc_points) - 1):
                if arc_points[i][0] <= now_utc <= arc_points[i + 1][0]:
                    frac = (now_utc - arc_points[i][0]).total_seconds() / max(
                        (arc_points[i + 1][0] - arc_points[i][0]).total_seconds(),
                        1,
                    )
                    cur_az = arc_points[i][1] + frac * (
                        arc_points[i + 1][1] - arc_points[i][1]
                    )
                    cur_el = arc_points[i][2] + frac * (
                        arc_points[i + 1][2] - arc_points[i][2]
                    )
                    px, py = _azel_to_xy(cur_az, cur_el, cx, cy, radius)
                    draw.ellipse(
                        [px - 7, py - 7, px + 7, py + 7],
                        fill=(255, 255, 0),
                        outline=(255, 255, 255),
                        width=2,
                    )
                    break

    def _draw_pass_info_panel(
        self,
        draw,
        w,
        h,
        pass_data,
        now_utc,
        timezone_name,
        time_format,
        during_pass,
    ):
        panel_x = int(w * 0.62)
        panel_w = w - panel_x - 10

        font_size = max(int(h * 0.05), 14)
        small_size = max(int(h * 0.04), 12)
        title_size = max(int(h * 0.06), 16)
        font = get_font("Jost", font_size)
        small_font = get_font("Jost", small_size)
        title_font = get_font("Jost", title_size, "bold")

        text_color = (255, 255, 255)
        accent_color = (100, 200, 100)

        try:
            import pytz

            tz = pytz.timezone(timezone_name)
        except Exception:
            tz = timezone.utc

        y = int(h * 0.1)
        line_h = int(font_size * 1.6)

        if during_pass:
            draw.text(
                (panel_x, y), "PASS IN PROGRESS", fill=(255, 200, 0), font=title_font
            )
        else:
            draw.text(
                (panel_x, y), "UPCOMING PASS", fill=accent_color, font=title_font
            )
        y += int(title_size * 2)

        rise_utc = pass_data["rise_utc"]
        set_utc = pass_data["set_utc"]
        max_el = pass_data.get("max_elevation", 0)
        duration_s = (set_utc - rise_utc).total_seconds()
        duration_str = f"{int(duration_s // 60)}m {int(duration_s % 60)}s"

        rise_local = rise_utc.astimezone(tz)
        set_local = set_utc.astimezone(tz)

        if time_format == "24h":
            rise_str = rise_local.strftime("%H:%M:%S")
            set_str = set_local.strftime("%H:%M:%S")
        else:
            rise_str = rise_local.strftime("%I:%M:%S %p").lstrip("0")
            set_str = set_local.strftime("%I:%M:%S %p").lstrip("0")

        if during_pass:
            time_left = (set_utc - now_utc).total_seconds()
            if time_left > 0:
                mins = int(time_left // 60)
                secs = int(time_left % 60)
                draw.text(
                    (panel_x, y),
                    f"Ends in: {mins}m {secs}s",
                    fill=text_color,
                    font=font,
                )
                y += line_h
        else:
            countdown = (rise_utc - now_utc).total_seconds()
            if countdown > 0:
                mins = int(countdown // 60)
                secs = int(countdown % 60)
                draw.text(
                    (panel_x, y),
                    f"Starts in: {mins}m {secs}s",
                    fill=text_color,
                    font=font,
                )
                y += line_h

        info_items = [
            ("Rise", rise_str),
            ("Set", set_str),
            ("Max Elevation", f"{max_el:.0f}\u00b0"),
            ("Duration", duration_str),
        ]

        # Direction to look
        rise_az = pass_data.get("rise_azimuth", 0)
        direction = _azimuth_to_compass(rise_az)
        info_items.append(("Look", f"{direction} ({rise_az:.0f}\u00b0)"))

        for label, value in info_items:
            draw.text(
                (panel_x, y), f"{label}:", fill=(150, 150, 150), font=small_font
            )
            draw.text(
                (panel_x + int(panel_w * 0.4), y),
                value,
                fill=text_color,
                font=small_font,
            )
            y += int(small_size * 1.5)

    # ───────── Post-pass Summary ─────────

    def _render_postpass(
        self,
        dimensions,
        pass_data,
        arc_points,
        now_utc,
        timezone_name,
        time_format,
    ):
        w, h = dimensions
        img = Image.new("RGB", dimensions, (0, 0, 0))
        draw = ImageDraw.Draw(img)

        plot_size = min(int(w * 0.55), h - 40)
        plot_cx = int(w * 0.3)
        plot_cy = h // 2
        plot_r = plot_size // 2 - 10

        self._draw_polar_grid(draw, plot_cx, plot_cy, plot_r)

        if pass_data:
            # Draw entire arc as completed (yellow)
            if arc_points:
                for i in range(len(arc_points) - 1):
                    x1, y1 = _azel_to_xy(
                        arc_points[i][1], arc_points[i][2], plot_cx, plot_cy, plot_r
                    )
                    x2, y2 = _azel_to_xy(
                        arc_points[i + 1][1],
                        arc_points[i + 1][2],
                        plot_cx,
                        plot_cy,
                        plot_r,
                    )
                    draw.line([(x1, y1), (x2, y2)], fill=(200, 200, 0), width=3)

                sx, sy = _azel_to_xy(
                    arc_points[0][1], arc_points[0][2], plot_cx, plot_cy, plot_r
                )
                draw.ellipse([sx - 5, sy - 5, sx + 5, sy + 5], fill=(0, 200, 0))

                ex, ey = _azel_to_xy(
                    arc_points[-1][1], arc_points[-1][2], plot_cx, plot_cy, plot_r
                )
                draw.ellipse([ex - 5, ey - 5, ex + 5, ey + 5], fill=(200, 0, 0))

        # Info panel
        panel_x = int(w * 0.62)
        font_size = max(int(h * 0.05), 14)
        small_size = max(int(h * 0.04), 12)
        title_size = max(int(h * 0.06), 16)
        title_font = get_font("Jost", title_size, "bold")
        font = get_font("Jost", font_size)
        small_font = get_font("Jost", small_size)

        y = int(h * 0.1)
        draw.text((panel_x, y), "PASS COMPLETE", fill=(100, 200, 100), font=title_font)
        y += int(title_size * 2)

        if pass_data:
            try:
                import pytz

                tz = pytz.timezone(timezone_name)
            except Exception:
                tz = timezone.utc

            max_el = pass_data.get("max_elevation", 0)
            duration_s = (
                pass_data["set_utc"] - pass_data["rise_utc"]
            ).total_seconds()
            duration_str = f"{int(duration_s // 60)}m {int(duration_s % 60)}s"

            rise_local = pass_data["rise_utc"].astimezone(tz)
            if time_format == "24h":
                rise_str = rise_local.strftime("%H:%M")
            else:
                rise_str = rise_local.strftime("%I:%M %p").lstrip("0")

            # Time since pass ended (updates in real-time)
            since_end = (now_utc - pass_data["set_utc"]).total_seconds()
            if since_end > 0:
                mins = int(since_end // 60)
                secs = int(since_end % 60)
                draw.text(
                    (panel_x, y),
                    f"Ended: {mins}m {secs}s ago",
                    fill=(255, 255, 255),
                    font=font,
                )
                y += int(font_size * 1.6)

            items = [
                ("Time", rise_str),
                ("Peak Elevation", f"{max_el:.0f}\u00b0"),
                ("Duration", duration_str),
            ]

            for label, value in items:
                draw.text(
                    (panel_x, y), f"{label}:", fill=(150, 150, 150), font=small_font
                )
                draw.text(
                    (panel_x + int((w - panel_x) * 0.4), y),
                    value,
                    fill=(255, 255, 255),
                    font=small_font,
                )
                y += int(small_size * 1.5)

        return img


# ═══════════ Helper Functions ═══════════


def _find_weather_location(device_config):
    """Search loop manager for a weather plugin instance with lat/lon configured.

    Returns (lat, lon, city_name) where city_name may be empty.
    """
    try:
        loop_manager = device_config.get_loop_manager()
        for loop in loop_manager.loops:
            for ref in loop.plugin_order:
                if ref.plugin_id == "weather" and ref.plugin_settings:
                    lat = ref.plugin_settings.get("latitude")
                    lon = ref.plugin_settings.get("longitude")
                    if lat is not None and lon is not None:
                        city = ref.plugin_settings.get("customTitle", "")
                        logger.info(f"ISS Tracker using weather plugin location: {lat}, {lon} ({city})")
                        return float(lat), float(lon), city
    except Exception as e:
        logger.debug(f"Could not find weather location: {e}")
    return 0.0, 0.0, ""


def _parse_float(val, default):
    try:
        if val is None or val == '':
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _parse_int(val, default):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _load_tle(cache_path):
    """Load TLE data, refreshing from CelesTrak if stale."""
    tle_lines = None
    cache_fresh = False

    # Try loading from cache
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cache = json.load(f)
            cached_time = cache.get("timestamp", 0)
            if time_module.time() - cached_time < TLE_CACHE_MAX_AGE:
                cache_fresh = True
            tle_lines = (cache["line1"], cache["line2"])
        except Exception as e:
            logger.warning(f"Failed to read TLE cache: {e}")

    # Fetch fresh TLE if needed
    if not cache_fresh:
        try:
            session = get_http_session()
            response = session.get(TLE_URL, timeout=15)
            response.raise_for_status()
            lines = response.text.strip().splitlines()
            if len(lines) >= 3:
                tle_lines = (lines[1].strip(), lines[2].strip())
            elif len(lines) >= 2:
                tle_lines = (lines[0].strip(), lines[1].strip())

            if tle_lines:
                cache_dir = os.path.dirname(cache_path)
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_path, "w") as f:
                    json.dump(
                        {
                            "line1": tle_lines[0],
                            "line2": tle_lines[1],
                            "timestamp": time_module.time(),
                        },
                        f,
                    )
                logger.info("TLE data refreshed from CelesTrak")
        except Exception as e:
            logger.warning(f"Failed to fetch TLE from CelesTrak: {e}")

    if not tle_lines:
        raise RuntimeError("No TLE data available for ISS")

    return tle_lines


def _compute_iss_position(tle_lines, dt_utc):
    """Compute ISS lat/lon/alt using sgp4 directly."""
    from sgp4.api import Satrec, WGS72

    sat = Satrec.twoline2rv(tle_lines[0], tle_lines[1], WGS72)

    # Convert datetime to Julian date
    jd, fr = _datetime_to_jd(dt_utc)
    e, r, v = sat.sgp4(jd, fr)

    if e != 0:
        raise RuntimeError(f"SGP4 propagation error code: {e}")

    # ECI to lat/lon/alt
    x, y, z = r  # km
    gmst = _gmst(jd, fr)

    # ECEF coordinates
    x_ecef = x * math.cos(gmst) + y * math.sin(gmst)
    y_ecef = -x * math.sin(gmst) + y * math.cos(gmst)
    z_ecef = z

    # Geodetic coordinates
    lon = math.degrees(math.atan2(y_ecef, x_ecef))
    lat = math.degrees(math.atan2(z_ecef, math.sqrt(x_ecef**2 + y_ecef**2)))
    alt = math.sqrt(x**2 + y**2 + z**2) - EARTH_RADIUS_KM

    return lat, lon, alt


def _datetime_to_jd(dt_utc):
    """Convert datetime to Julian date (jd, fraction) pair."""
    y = dt_utc.year
    m = dt_utc.month
    d = dt_utc.day
    if m <= 2:
        y -= 1
        m += 12
    A = int(y / 100)
    B = 2 - A + int(A / 4)
    jd = int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + B - 1524.5

    fr = (
        dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0
    ) / 24.0 + dt_utc.microsecond / (24.0 * 3600.0 * 1e6)

    return jd, fr


def _gmst(jd, fr):
    """Calculate Greenwich Mean Sidereal Time in radians."""
    T = ((jd - 2451545.0) + fr) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * ((jd - 2451545.0) + fr)
        + 0.000387933 * T**2
        - T**3 / 38710000.0
    )
    return math.radians(gmst_deg % 360)


def _footprint_radius(alt_km):
    """Calculate ISS footprint radius in degrees."""
    rho = math.acos(EARTH_RADIUS_KM / (EARTH_RADIUS_KM + alt_km))
    return math.degrees(rho)


def _orbital_speed(alt_km):
    """Calculate orbital speed in km/h."""
    mu = 398600.4418  # Earth's gravitational parameter km^3/s^2
    r = EARTH_RADIUS_KM + alt_km
    v = math.sqrt(mu / r)  # km/s
    return v * 3600  # km/h


def _compute_ground_track(tle_lines, now_utc):
    """Compute future ground track points (next ~90 min). Cacheable."""
    if not tle_lines:
        return []
    points = []
    for minutes_ahead in range(0, 95, 2):
        t = now_utc + timedelta(minutes=minutes_ahead)
        try:
            lat, lon, _ = _compute_iss_position(tle_lines, t)
            points.append((lat, lon))
        except Exception:
            break
    return points


def _reverse_geocode_from_data(lat, lon, landmarks):
    """Find nearest landmark using pre-loaded landmarks data."""
    if not landmarks:
        return _ocean_fallback(lat, lon)
    min_dist = float("inf")
    nearest = None
    for lm in landmarks:
        d = _haversine(lat, lon, lm["lat"], lm["lon"])
        if d < min_dist:
            min_dist = d
            nearest = lm
    if nearest and min_dist < 1000:
        return f"{min_dist:.0f} km from {nearest['name']}"
    return _ocean_fallback(lat, lon)


def _nearest_city_from_data(lat, lon, landmarks):
    """Find nearest city name from pre-loaded landmarks data."""
    if not landmarks:
        return ""
    min_dist = float("inf")
    nearest = None
    for lm in landmarks:
        d = _haversine(lat, lon, lm["lat"], lm["lon"])
        if d < min_dist:
            min_dist = d
            nearest = lm
    if nearest:
        return nearest["name"].split(",")[0].strip()
    return ""


def _get_crew_count():
    """Get current ISS crew count from Open Notify API."""
    try:
        session = get_http_session()
        response = session.get(CREW_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        return sum(1 for p in data.get("people", []) if p.get("craft") == "ISS")
    except Exception as e:
        logger.warning(f"Failed to get crew count: {e}")
        return 0


def _ocean_fallback(lat, lon):
    """Simple ocean basin identification."""
    oceans = [
        ("North Pacific Ocean", 0, 90, 100, 260),
        ("South Pacific Ocean", -90, 0, 140, 290),
        ("North Atlantic Ocean", 0, 90, 280, 360),
        ("North Atlantic Ocean", 0, 90, 0, 10),
        ("South Atlantic Ocean", -90, 0, 290, 360),
        ("South Atlantic Ocean", -90, 0, 0, 20),
        ("Indian Ocean", -90, 30, 20, 140),
        ("Arctic Ocean", 66, 90, 0, 360),
        ("Southern Ocean", -90, -60, 0, 360),
    ]
    for name, lat_min, lat_max, lon_min, lon_max in oceans:
        norm_lon = lon % 360
        if lat_min <= lat <= lat_max and lon_min <= norm_lon <= lon_max:
            return name
    return f"{abs(lat):.1f}\u00b0{'N' if lat >= 0 else 'S'}, {abs(lon):.1f}\u00b0{'E' if lon >= 0 else 'W'}"


def _haversine(lat1, lon1, lat2, lon2):
    """Distance in km between two lat/lon points."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _predict_passes(tle_lines, obs_lat, obs_lon, now_utc, n2yo_api_key=None):
    """Predict visible ISS passes using Skyfield."""
    passes = []

    try:
        passes = _predict_passes_skyfield(tle_lines, obs_lat, obs_lon, now_utc)
    except Exception as e:
        logger.warning(f"Skyfield pass prediction failed: {e}")
        if n2yo_api_key:
            try:
                passes = _predict_passes_n2yo(obs_lat, obs_lon, n2yo_api_key)
            except Exception as e2:
                logger.warning(f"N2YO fallback also failed: {e2}")

    return passes


def _predict_passes_skyfield(tle_lines, obs_lat, obs_lon, now_utc):
    """Use Skyfield's find_events() for pass prediction with visibility check."""
    from skyfield.api import load, wgs84, EarthSatellite

    ts = load.timescale()
    sat = EarthSatellite(tle_lines[0], tle_lines[1], "ISS", ts)
    observer = wgs84.latlon(obs_lat, obs_lon)

    t0 = ts.from_datetime(now_utc)
    t1 = ts.from_datetime(now_utc + timedelta(days=10))

    t_events, events = sat.find_events(observer, t0, t1, altitude_degrees=10.0)

    # Load ephemeris once for sunlit/sun-altitude checks
    eph = load("de421.bsp")

    passes = []
    current_pass = {}
    culmination_ti = None
    for ti, event in zip(t_events, events):
        dt = ti.utc_datetime()
        if event == 0:  # rise
            current_pass = {"rise_utc": dt}
            culmination_ti = None
            difference = sat - observer
            topocentric = difference.at(ti)
            alt_deg, az_deg, _ = topocentric.altaz()
            current_pass["rise_azimuth"] = az_deg.degrees
        elif event == 1:  # culmination
            if current_pass:
                current_pass["culmination_utc"] = dt
                culmination_ti = ti
                difference = sat - observer
                topocentric = difference.at(ti)
                alt_deg, az_deg, _ = topocentric.altaz()
                current_pass["max_elevation"] = alt_deg.degrees
        elif event == 2:  # set
            if current_pass and "rise_utc" in current_pass:
                current_pass["set_utc"] = dt
                difference = sat - observer
                topocentric = difference.at(ti)
                alt_deg, az_deg, _ = topocentric.altaz()
                current_pass["set_azimuth"] = az_deg.degrees
                current_pass.setdefault("max_elevation", 10)
                current_pass.setdefault("rise_azimuth", 0)

                # Visibility check at culmination (peak of pass)
                visible = False
                if culmination_ti is not None:
                    try:
                        # Check if ISS is sunlit at peak
                        diff_at_peak = (sat - observer).at(culmination_ti)
                        iss_sunlit = diff_at_peak.is_sunlit(eph)

                        # Check if observer is in darkness (sun below -6° = civil twilight)
                        sun = eph["earth"].at(culmination_ti).observe(eph["sun"])
                        # Use observer's position for sun altitude
                        obs_location = eph["earth"] + observer
                        sun_from_obs = obs_location.at(culmination_ti).observe(eph["sun"])
                        sun_alt, _, _ = sun_from_obs.apparent().altaz()
                        observer_dark = sun_alt.degrees < -6.0

                        visible = bool(iss_sunlit) and observer_dark
                    except Exception as e:
                        logger.debug(f"Visibility check failed for pass: {e}")

                current_pass["visible"] = visible
                passes.append(current_pass)
                current_pass = {}
                culmination_ti = None

    visible_count = sum(1 for p in passes if p.get("visible"))
    logger.info(f"Pass prediction: {len(passes)} total, {visible_count} visible")
    return passes


def _predict_passes_n2yo(obs_lat, obs_lon, api_key):
    """Fallback: use N2YO API for pass prediction."""
    url = (
        f"https://api.n2yo.com/rest/v1/satellite/visualpasses/"
        f"{ISS_CATALOG_NUMBER}/{obs_lat}/{obs_lon}/0/7/10/&apiKey={api_key}"
    )
    session = get_http_session()
    response = session.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()

    passes = []
    for p in data.get("passes", []):
        rise_utc = datetime.fromtimestamp(p["startUTC"], tz=timezone.utc)
        set_utc = datetime.fromtimestamp(p["endUTC"], tz=timezone.utc)
        passes.append(
            {
                "rise_utc": rise_utc,
                "set_utc": set_utc,
                "max_elevation": p.get("maxEl", 0),
                "rise_azimuth": p.get("startAz", 0),
                "set_azimuth": p.get("endAz", 0),
                "visible": True,  # N2YO visualpasses endpoint only returns visible passes
            }
        )
    return passes


def _determine_mode(now_utc, passes, prepass_minutes):
    """Determine display mode based on visible pass timing."""
    for p in passes:
        if not p.get("visible"):
            continue
        rise = p["rise_utc"]
        sett = p["set_utc"]

        # Post-pass: within 5 minutes after pass ended
        if sett <= now_utc <= sett + timedelta(minutes=POSTPASS_DURATION):
            return "postpass"

        # Pre-pass or during pass
        if rise - timedelta(minutes=prepass_minutes) <= now_utc <= sett:
            return "prepass"

    return "nadir"


def _get_active_pass(now_utc, passes, prepass_minutes):
    """Get the visible pass that is currently active or upcoming within trigger window."""
    for p in passes:
        if not p.get("visible"):
            continue
        rise = p["rise_utc"]
        sett = p["set_utc"]
        if rise - timedelta(minutes=prepass_minutes) <= now_utc <= sett:
            return p
    return None


def _get_recent_pass(now_utc, passes):
    """Get the visible pass that just ended (within POSTPASS_DURATION)."""
    for p in passes:
        if not p.get("visible"):
            continue
        sett = p["set_utc"]
        if sett <= now_utc <= sett + timedelta(minutes=POSTPASS_DURATION):
            return p
    return None


def _is_during_pass(now_utc, pass_data):
    """Check if currently during a pass (between rise and set)."""
    if not pass_data:
        return False
    return pass_data["rise_utc"] <= now_utc <= pass_data["set_utc"]


def _compute_pass_arc(tle_lines, pass_data, obs_lat, obs_lon):
    """Compute az/el arc points for a pass."""
    try:
        from skyfield.api import load, wgs84, EarthSatellite

        ts = load.timescale()
        sat = EarthSatellite(tle_lines[0], tle_lines[1], "ISS", ts)
        observer = wgs84.latlon(obs_lat, obs_lon)

        rise = pass_data["rise_utc"]
        sett = pass_data["set_utc"]
        duration = (sett - rise).total_seconds()
        steps = max(int(duration / 5), 10)  # point every ~5 seconds

        # Load ephemeris ONCE outside the loop (de421.bsp is ~30MB)
        eph = load("de421.bsp")

        arc = []
        for i in range(steps + 1):
            frac = i / steps
            t = rise + timedelta(seconds=frac * duration)
            t_sky = ts.from_datetime(t)

            difference = sat - observer
            topocentric = difference.at(t_sky)
            alt_deg, az_deg, _ = topocentric.altaz()

            sunlit = topocentric.is_sunlit(eph)

            arc.append((t, az_deg.degrees, alt_deg.degrees, bool(sunlit)))

        return arc
    except Exception as e:
        logger.warning(f"Failed to compute pass arc: {e}")
        return []


def _azel_to_xy(az, el, cx, cy, radius):
    """Convert azimuth/elevation to x,y on polar plot. N=up, E=right."""
    r = radius * (90 - el) / 90
    angle_rad = math.radians(az - 90)  # Rotate so N is up
    x = cx + r * math.cos(angle_rad)
    y = cy + r * math.sin(angle_rad)
    return int(x), int(y)


def _azimuth_to_compass(az):
    """Convert azimuth degrees to compass direction."""
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    ix = round(az / 22.5) % 16
    return dirs[ix]
