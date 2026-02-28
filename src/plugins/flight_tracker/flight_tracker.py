import logging
import math
import os
import threading
import time as time_module

from PIL import Image, ImageDraw
from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import get_font
from utils.http_client import get_http_session
from utils.text_utils import get_text_dimensions, truncate_text

logger = logging.getLogger(__name__)

# API endpoints (both return ADS-B Exchange v2 compatible JSON)
ADSBFI_URL = "https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{nm}"
AIRPLANESLIVE_URL = "https://api.airplanes.live/v2/point/{lat}/{lon}/{radius}"

# OpenStreetMap tile server
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_SIZE = 256

# Display limits
MAX_AIRCRAFT_DISPLAY = 30  # Max markers on map
MAX_AIRCRAFT_INFO = 6  # Shown in info strip (2 columns x 3 rows)

# Map cache directory relative to plugin
MAP_CACHE_DIR = "resources/map_cache"

# Earth radius for distance calculations
EARTH_RADIUS_NM = 3440.065

# Trail/extrapolation constants
MAX_TRAIL_POINTS = 20  # Max positions per aircraft trail
STALE_GENERATIONS = 2  # Prune after this many missed API fetches
MAX_EXTRAPOLATION_SEC = 120  # Stop extrapolating after 2 min without API data


def _aircraft_id(ac):
    """Return a stable identifier for trail/extrapolation keying."""
    return ac.get("hex") or ac.get("registration") or ac.get("callsign") or None


class FlightTracker(BasePlugin):
    def __init__(self, config, **dependencies):
        super().__init__(config, **dependencies)

        self._lock = threading.Lock()

        # Cached API response
        self._cached_aircraft = None
        self._last_fetch_time = 0  # time.monotonic()
        self._last_fetch_params = None  # (lat, lon, radius, source)

        # Trail accumulation: {aircraft_id: {"points": [(lat, lon, mono_time), ...], "last_seen_gen": int}}
        self._trails = {}
        self._fetch_generation = 0

        # Extrapolation base: {aircraft_id: {"lat", "lon", "heading", "speed_kts", "fetch_time"}}
        self._extrapolation_base = {}

        # Cached base layer (map + dim overlay + range ring + crosshair)
        self._base_layer = None
        self._base_layer_key = None

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = False
        template_params['hide_refresh_interval'] = True
        return template_params

    def generate_image(self, settings, device_config):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        logger.info("=== Flight Tracker Plugin: Starting ===")

        # Parse location
        lat = _parse_float(settings.get("latitude"), None)
        lon = _parse_float(settings.get("longitude"), None)

        if lat is None or lon is None:
            w_lat, w_lon = _find_weather_location(device_config)
            if lat is None:
                lat = w_lat
            if lon is None:
                lon = w_lon

        if lat is None or lon is None:
            return self._render_error(dimensions, "No location configured",
                                      "Set a location in plugin settings or configure the Weather plugin.")

        # Parse settings
        radius_nm = _parse_int(settings.get("radius"), 100)
        units = settings.get("units", "aviation")
        zoom = _parse_int(settings.get("mapZoom"), 8)
        hide_ground = settings.get("hideGround") in ("on", "true", True, "on")
        show_tracks = settings.get("showTracks") not in ("false", False)
        source = settings.get("dataSource", "auto")
        data_interval = _parse_float(settings.get("dataRefreshInterval"), 30)

        # Fetch or reuse cached data
        current_params = (lat, lon, radius_nm, source)
        now = time_module.monotonic()

        with self._lock:
            params_changed = (self._last_fetch_params != current_params)
            time_elapsed = (now - self._last_fetch_time) >= data_interval
            needs_fetch = params_changed or time_elapsed or self._cached_aircraft is None

            if params_changed and self._cached_aircraft is not None:
                # Location/radius changed — clear stale state
                self._trails.clear()
                self._extrapolation_base.clear()
                logger.info("Params changed, cleared trails and extrapolation state")

            if needs_fetch:
                aircraft = _fetch_aircraft(lat, lon, radius_nm, source)
                if aircraft is not None:
                    self._cached_aircraft = aircraft
                    self._last_fetch_time = now
                    self._last_fetch_params = current_params
                    self._fetch_generation += 1
                    self._update_trails(aircraft, now)
                    self._update_extrapolation_base(aircraft, now)
                    logger.info(f"API fetch: {len(aircraft)} aircraft, generation {self._fetch_generation}")
                elif self._cached_aircraft is not None:
                    aircraft = self._cached_aircraft
                    logger.warning("API fetch failed, using cached data with extrapolation")
                else:
                    return self._render_error(dimensions, "No data available",
                                              "Could not reach flight tracking API. Check internet connection.")
            else:
                aircraft = self._cached_aircraft
                elapsed = now - self._last_fetch_time
                logger.info(f"Using cached data ({elapsed:.1f}s old), extrapolating positions")

            # Apply dead-reckoning extrapolation
            aircraft = self._extrapolate_positions(aircraft, now, lat, lon)

            # Inject accumulated trails
            aircraft = self._inject_trails(aircraft)

        # Filter and sort
        if hide_ground:
            aircraft = [a for a in aircraft if not a.get("on_ground", False)]
        aircraft.sort(key=lambda a: a.get("distance_nm", 9999))
        aircraft = aircraft[:MAX_AIRCRAFT_DISPLAY]

        logger.info(f"Rendering {len(aircraft)} aircraft within {radius_nm}nm")

        # Render
        w, h = dimensions
        info_h = max(int(h * 0.20), 100)
        map_h = h - info_h

        # Get cached base layer (map + dim + range ring + crosshair)
        cache_dir = os.path.join(self.get_plugin_dir(), MAP_CACHE_DIR)
        os.makedirs(cache_dir, exist_ok=True)
        base = self._get_base_layer(lat, lon, zoom, radius_nm, w, map_h, dimensions, cache_dir)
        img = base.copy()
        draw = ImageDraw.Draw(img)

        # Draw track history lines (behind markers)
        if show_tracks:
            for ac in aircraft:
                _draw_aircraft_trail(draw, ac, lat, lon, zoom, w, map_h)

        # Plot aircraft markers
        for ac in aircraft:
            _draw_aircraft_marker(draw, ac, lat, lon, zoom, w, map_h, units)

        # Draw info strip
        self._draw_info_strip(draw, w, h, info_h, map_h, aircraft, units, radius_nm, lat, lon)

        logger.info("=== Flight Tracker Plugin: Complete ===")
        return img.convert("RGB")

    # ─────────────────── Base Layer Cache ───────────────────

    def _get_base_layer(self, lat, lon, zoom, radius_nm, w, map_h, dimensions, cache_dir):
        """Return cached base layer (map + dim + range ring + crosshair). Renders on first call or when params change."""
        cache_key = (lat, lon, zoom, radius_nm, w, map_h)

        if self._base_layer is not None and self._base_layer_key == cache_key:
            return self._base_layer

        logger.info("Rendering new base layer (map + overlay)")
        map_img = _get_map_image(lat, lon, zoom, w, map_h, cache_dir)

        base = Image.new("RGBA", dimensions, (20, 25, 35, 255))
        base.paste(map_img, (0, 0))

        dim_overlay = Image.new("RGBA", (w, map_h), (0, 0, 0, 120))
        base.alpha_composite(dim_overlay, (0, 0))

        draw = ImageDraw.Draw(base)
        _draw_range_ring(draw, lat, lon, radius_nm, zoom, w, map_h)
        _draw_center_marker(draw, w, map_h)

        self._base_layer = base
        self._base_layer_key = cache_key
        return self._base_layer

    # ─────────────────── State Management ───────────────────

    def _update_trails(self, aircraft, fetch_time):
        """Append current API positions to trail history. Called under self._lock."""
        generation = self._fetch_generation

        for ac in aircraft:
            aid = _aircraft_id(ac)
            if not aid:
                continue

            if aid not in self._trails:
                self._trails[aid] = {"points": [], "last_seen_gen": generation}

            entry = self._trails[aid]
            entry["last_seen_gen"] = generation
            entry["points"].append((ac["lat"], ac["lon"], fetch_time))

            if len(entry["points"]) > MAX_TRAIL_POINTS:
                entry["points"] = entry["points"][-MAX_TRAIL_POINTS:]

        # Prune aircraft not seen in recent fetches
        stale_ids = [
            aid for aid, entry in self._trails.items()
            if generation - entry["last_seen_gen"] > STALE_GENERATIONS
        ]
        for aid in stale_ids:
            del self._trails[aid]
            self._extrapolation_base.pop(aid, None)

        if stale_ids:
            logger.info(f"Pruned {len(stale_ids)} stale aircraft from trails")

    def _update_extrapolation_base(self, aircraft, fetch_time):
        """Snapshot current positions/velocities for dead reckoning. Called under self._lock."""
        for ac in aircraft:
            aid = _aircraft_id(ac)
            if not aid:
                continue
            heading = ac.get("heading")
            speed = ac.get("speed")
            if heading is not None and speed is not None and speed > 0 and not ac.get("on_ground"):
                self._extrapolation_base[aid] = {
                    "lat": ac["lat"],
                    "lon": ac["lon"],
                    "heading": heading,
                    "speed_kts": speed,
                    "fetch_time": fetch_time,
                }
            else:
                self._extrapolation_base.pop(aid, None)

    def _extrapolate_positions(self, aircraft, now, user_lat, user_lon):
        """Apply dead reckoning to shift aircraft positions forward. Called under self._lock."""
        result = []
        for ac in aircraft:
            ac = dict(ac)  # shallow copy to avoid mutating cache
            aid = _aircraft_id(ac)
            base = self._extrapolation_base.get(aid) if aid else None

            if base and base["speed_kts"] > 0:
                elapsed = now - base["fetch_time"]
                if 0 < elapsed < MAX_EXTRAPOLATION_SEC:
                    distance_nm = base["speed_kts"] * (elapsed / 3600.0)
                    heading_rad = math.radians(base["heading"])
                    lat_rad = math.radians(base["lat"])

                    dlat = (distance_nm / 60.0) * math.cos(heading_rad)
                    dlon = (distance_nm / 60.0) * math.sin(heading_rad) / max(math.cos(lat_rad), 0.01)

                    ac["lat"] = base["lat"] + dlat
                    ac["lon"] = base["lon"] + dlon
                    ac["distance_nm"] = _haversine_nm(user_lat, user_lon, ac["lat"], ac["lon"])

            result.append(ac)
        return result

    def _inject_trails(self, aircraft):
        """Replace each aircraft's trail with accumulated trail data plus current position."""
        result = []
        for ac in aircraft:
            ac = dict(ac)
            aid = _aircraft_id(ac)
            trail_points = []
            if aid and aid in self._trails:
                trail_points = [{"lat": p[0], "lon": p[1]} for p in self._trails[aid]["points"]]
            # Append current (possibly extrapolated) position so trail extends to marker
            if ac.get("lat") and ac.get("lon"):
                trail_points.append({"lat": ac["lat"], "lon": ac["lon"]})
            if trail_points:
                ac["trail"] = trail_points
            result.append(ac)
        return result

    # ─────────────────── Rendering ───────────────────

    def _render_error(self, dimensions, title, message):
        """Render an error screen when data is unavailable."""
        w, h = dimensions
        img = Image.new("RGB", dimensions, (20, 25, 35))
        draw = ImageDraw.Draw(img)

        title_font = get_font("Jost", max(int(h * 0.05), 20), "bold")
        msg_font = get_font("Jost", max(int(h * 0.03), 14))

        tw = get_text_dimensions(draw, title, title_font)[0]
        draw.text(((w - tw) // 2, int(h * 0.35)), title, fill=(255, 100, 100), font=title_font)

        mw = get_text_dimensions(draw, message, msg_font)[0]
        draw.text(((w - mw) // 2, int(h * 0.45)), message, fill=(180, 180, 180), font=msg_font)

        return img

    def _draw_info_strip(self, draw, w, h, info_h, map_h, aircraft, units, radius_nm, lat, lon):
        """Draw the bottom info panel with aircraft details in a 2-column layout."""
        # Background
        draw.rectangle([(0, map_h), (w, h)], fill=(15, 18, 25))
        draw.line([(0, map_h), (w, map_h)], fill=(60, 65, 80), width=2)

        is_vertical = h > w

        padding = int(w * 0.02)
        font_size = max(int(info_h * 0.18), 12)
        small_size = max(int(info_h * 0.15), 10)
        header_font = get_font("Jost", font_size, "bold")
        font = get_font("Jost", small_size)
        small_font = get_font("Jost", max(small_size - 2, 9))

        text_color = (220, 220, 220)
        accent_color = (80, 200, 255)
        dim_color = (130, 135, 150)

        y_start = map_h + int(info_h * 0.06)
        line_h = int(font_size * 1.15)

        # Header line
        count = len(aircraft)
        unit_label = _radius_unit_label(units)
        radius_display = _convert_distance(radius_nm, units)
        header = f"{count} aircraft within {radius_display} {unit_label}"
        hw = get_text_dimensions(draw, header, header_font)[0]
        draw.text(((w - hw) // 2, y_start), header, fill=accent_color, font=header_font)

        # Vertical mode: simplified info strip (full details in horizontal only)
        if is_vertical:
            hint = "Aircraft details available in horizontal mode"
            hint_w = draw.textbbox((0, 0), hint, font=font)[2]
            draw.text(
                ((w - hint_w) // 2, y_start + line_h + 2),
                hint,
                fill=(120, 120, 120),
                font=font,
            )
            return

        # Right side: location
        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"
        coord_str = f"{abs(lat):.2f}{lat_dir}, {abs(lon):.2f}{lon_dir}"
        cw = get_text_dimensions(draw, coord_str, small_font)[0]
        draw.text((w - padding - cw, y_start + 2), coord_str, fill=dim_color, font=small_font)

        if not aircraft:
            draw.text((padding, y_start + line_h + 2), "No aircraft detected in range", fill=dim_color, font=font)
            return

        # 2-column layout: 3 aircraft per column, 6 total
        max_display = 6
        display_count = min(max_display, len(aircraft))
        rows_per_col = 3
        col_width = (w - padding * 3) // 2

        # Vertical divider between columns
        divider_x = padding + col_width + padding // 2
        data_y_start = y_start + line_h + 2
        divider_y1 = data_y_start - 2
        divider_y2 = data_y_start + rows_per_col * line_h - 4
        draw.line([(divider_x, divider_y1), (divider_x, divider_y2)], fill=(60, 65, 80), width=1)

        # Column field widths: "ID - Type ▲" then altitude then speed
        inner_pad = int(w * 0.025)
        indicator_w = int(small_size * 0.9)
        usable_col = col_width - inner_pad * 2
        id_w = int(usable_col * 0.46)
        alt_w = int(usable_col * 0.27)
        speed_w = int(usable_col * 0.27)

        for i in range(display_count):
            col = i // rows_per_col  # 0 = left, 1 = right
            row = i % rows_per_col
            x_base = padding + col * (col_width + padding) + inner_pad
            y = data_y_start + row * line_h
            x = x_base

            ac = aircraft[i]

            # Colored vertical rate indicator first
            _draw_vert_indicator(draw, ac, x, y, small_size, font)
            x += indicator_w + int(small_size * 0.4)

            # "ID | Type" label (e.g., "AAL2228 | A319")
            callsign = ac.get("callsign", "???").strip() or "???"
            ac_type = ac.get("aircraft_type", "")
            if ac_type:
                id_label = f"{callsign} | {ac_type}"
            else:
                id_label = callsign
            id_text = truncate_text(draw, id_label, font, id_w - indicator_w - 4)
            draw.text((x, y), id_text, fill=(255, 255, 255), font=font)
            x = x_base + id_w

            # Altitude
            alt = ac.get("altitude")
            if alt is not None and alt != "ground":
                draw.text((x, y), _format_altitude(alt, units), fill=text_color, font=font)
            elif alt == "ground":
                draw.text((x, y), "GND", fill=dim_color, font=font)
            x += alt_w

            # Speed
            speed = ac.get("speed")
            if speed is not None:
                draw.text((x, y), _format_speed(speed, units), fill=text_color, font=font)



# ─────────────────── Data Fetching ───────────────────

def _fetch_aircraft(lat, lon, radius_nm, source="auto"):
    """Fetch aircraft data from ADS-B API with fallback."""
    session = get_http_session()

    apis = []
    if source == "adsbfi":
        apis = [("adsb.fi", ADSBFI_URL)]
    elif source == "airplaneslive":
        apis = [("airplanes.live", AIRPLANESLIVE_URL)]
    else:
        apis = [("adsb.fi", ADSBFI_URL), ("airplanes.live", AIRPLANESLIVE_URL)]

    for name, url_template in apis:
        try:
            url = url_template.format(lat=lat, lon=lon, nm=radius_nm, radius=radius_nm)
            logger.info(f"Fetching aircraft from {name}: {url}")
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            aircraft_list = data.get("ac", [])
            if aircraft_list is None:
                aircraft_list = []

            result = []
            for ac in aircraft_list:
                parsed = _parse_aircraft(ac, lat, lon)
                if parsed:
                    result.append(parsed)

            logger.info(f"Got {len(result)} aircraft from {name}")
            return result
        except Exception as e:
            logger.warning(f"Failed to fetch from {name}: {e}")
            continue

    logger.error("All flight data sources failed")
    return None


def _parse_aircraft(ac, user_lat, user_lon):
    """Parse a single aircraft record from ADS-B Exchange v2 format."""
    ac_lat = ac.get("lat")
    ac_lon = ac.get("lon")
    if ac_lat is None or ac_lon is None:
        return None

    try:
        ac_lat = float(ac_lat)
        ac_lon = float(ac_lon)
    except (ValueError, TypeError):
        return None

    # Altitude: can be int, float, or "ground"
    alt_baro = ac.get("alt_baro")
    alt_geom = ac.get("alt_geom")
    if alt_baro == "ground":
        altitude = "ground"
        on_ground = True
    elif alt_baro is not None:
        try:
            altitude = int(float(alt_baro))
            on_ground = False
        except (ValueError, TypeError):
            altitude = None
            on_ground = False
    elif alt_geom is not None:
        try:
            altitude = int(float(alt_geom))
            on_ground = False
        except (ValueError, TypeError):
            altitude = None
            on_ground = False
    else:
        altitude = None
        on_ground = bool(ac.get("ground", False))

    callsign = (ac.get("flight") or ac.get("callsign") or "").strip()
    speed_kts = ac.get("gs")  # ground speed in knots
    heading = ac.get("track") or ac.get("true_heading")
    ac_type = ac.get("t", "")  # aircraft type designator
    registration = ac.get("r", "")
    vert_rate = ac.get("baro_rate") or ac.get("geom_rate")  # ft/min

    distance_nm = _haversine_nm(user_lat, user_lon, ac_lat, ac_lon)

    return {
        "hex": ac.get("hex", ""),
        "callsign": callsign,
        "lat": ac_lat,
        "lon": ac_lon,
        "altitude": altitude,
        "speed": _parse_float(speed_kts, None),
        "heading": _parse_float(heading, None),
        "aircraft_type": ac_type,
        "registration": registration,
        "on_ground": on_ground,
        "distance_nm": distance_nm,
        "vert_rate": _parse_float(vert_rate, None),
        "trail": [],
    }


# ─────────────────── Map Rendering ───────────────────

def _get_map_image(lat, lon, zoom, width, height, cache_dir):
    """Load cached map composite or generate from OSM tiles."""
    cache_key = f"{lat:.3f}_{lon:.3f}_{zoom}"
    cache_path = os.path.join(cache_dir, f"{cache_key}.png")

    if os.path.exists(cache_path):
        try:
            cached = Image.open(cache_path).convert("RGB")
            return _crop_to_viewport(cached, lat, lon, zoom, width, height)
        except Exception as e:
            logger.warning(f"Failed to load cached map: {e}")

    try:
        composite = _generate_map_composite(lat, lon, zoom, cache_dir, cache_path)
        return _crop_to_viewport(composite, lat, lon, zoom, width, height)
    except Exception as e:
        logger.error(f"Failed to generate map: {e}")
        return Image.new("RGB", (width, height), (30, 35, 50))


def _generate_map_composite(lat, lon, zoom, cache_dir, cache_path):
    """Fetch OSM tiles and stitch into a single composite image."""
    session = get_http_session()

    n = 2 ** zoom
    center_tx = int((lon + 180) / 360 * n)
    center_ty = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)

    grid_radius = 3
    tiles_across = grid_radius * 2 + 1
    composite_size = tiles_across * TILE_SIZE
    composite = Image.new("RGB", (composite_size, composite_size), (30, 35, 50))

    tiles_fetched = 0
    for dy in range(-grid_radius, grid_radius + 1):
        for dx in range(-grid_radius, grid_radius + 1):
            tx = (center_tx + dx) % n
            ty = center_ty + dy
            if ty < 0 or ty >= n:
                continue

            url = TILE_URL.format(z=zoom, x=tx, y=ty)
            try:
                resp = session.get(url, timeout=10)
                resp.raise_for_status()
                from io import BytesIO
                tile = Image.open(BytesIO(resp.content)).convert("RGB")

                px = (dx + grid_radius) * TILE_SIZE
                py = (dy + grid_radius) * TILE_SIZE
                composite.paste(tile, (px, py))
                tiles_fetched += 1

                time_module.sleep(0.1)
            except Exception as e:
                logger.debug(f"Failed to fetch tile z={zoom} x={tx} y={ty}: {e}")

    logger.info(f"Map composite generated: {tiles_fetched} tiles at zoom {zoom}")

    try:
        composite.save(cache_path, "PNG")
        logger.info(f"Map cached to {cache_path}")
    except Exception as e:
        logger.warning(f"Failed to cache map: {e}")

    return composite


def _crop_to_viewport(composite, lat, lon, zoom, vw, vh):
    """Crop the cached tile composite to a viewport centered on lat/lon."""
    n = 2 ** zoom
    cw, ch = composite.size
    tiles_across = cw // TILE_SIZE
    grid_radius = tiles_across // 2

    center_tx_frac = (lon + 180) / 360 * n
    center_ty_frac = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n

    center_tx_int = int(center_tx_frac)
    center_ty_int = int(center_ty_frac)

    frac_x = center_tx_frac - center_tx_int
    frac_y = center_ty_frac - center_ty_int

    cpx = int(grid_radius * TILE_SIZE + frac_x * TILE_SIZE)
    cpy = int(grid_radius * TILE_SIZE + frac_y * TILE_SIZE)

    x1 = cpx - vw // 2
    y1 = cpy - vh // 2
    x2 = x1 + vw
    y2 = y1 + vh

    if x1 < 0:
        x1, x2 = 0, vw
    if y1 < 0:
        y1, y2 = 0, vh
    if x2 > cw:
        x1, x2 = cw - vw, cw
    if y2 > ch:
        y1, y2 = ch - vh, ch

    viewport = composite.crop((x1, y1, x2, y2))
    if viewport.size != (vw, vh):
        viewport = viewport.resize((vw, vh), Image.LANCZOS)
    return viewport


# ─────────────────── Aircraft Markers ───────────────────

def _draw_vert_indicator(draw, ac, x, y, font_size, font):
    """Draw a small colored arrow/dash indicating climb, descent, or level flight."""
    color = _get_aircraft_color(ac)
    vert_rate = ac.get("vert_rate")
    cx = x + font_size // 3
    cy = y + font_size // 2 + 1
    sz = max(font_size // 3, 3)

    if vert_rate is not None and vert_rate > 300:
        # Up arrow triangle
        draw.polygon([(cx, cy - sz), (cx - sz, cy + sz), (cx + sz, cy + sz)], fill=color)
    elif vert_rate is not None and vert_rate < -300:
        # Down arrow triangle
        draw.polygon([(cx, cy + sz), (cx - sz, cy - sz), (cx + sz, cy - sz)], fill=color)
    else:
        # Level dash
        draw.line([(cx - sz, cy), (cx + sz, cy)], fill=color, width=2)


def _get_aircraft_color(ac):
    """Get marker color based on vertical rate."""
    vert_rate = ac.get("vert_rate")
    if vert_rate is not None:
        if vert_rate > 300:
            return (100, 220, 100)  # Climbing - green
        elif vert_rate < -300:
            return (255, 130, 80)  # Descending - orange
    return (80, 200, 255)  # Level/default - cyan


def _draw_aircraft_trail(draw, ac, center_lat, center_lon, zoom, vw, vh):
    """Draw a dotted trail line showing aircraft's recent path."""
    trail = ac.get("trail")
    if not trail or len(trail) < 2:
        return

    # Skip trail if the aircraft itself has left the viewport
    ac_px, ac_py = _latlon_to_pixel(ac["lat"], ac["lon"], center_lat, center_lon, zoom, vw, vh)
    if ac_px < -50 or ac_px > vw + 50 or ac_py < -50 or ac_py > vh + 50:
        return

    color = _get_aircraft_color(ac)
    shadow_color = (0, 0, 0)

    points = []
    for pt in trail[-MAX_TRAIL_POINTS:]:
        px, py = _latlon_to_pixel(pt["lat"], pt["lon"], center_lat, center_lon, zoom, vw, vh)
        if -200 < px < vw + 200 and -200 < py < vh + 200:
            points.append((px, py))

    # Draw dashed trail: dark shadow first, then colored line on top
    for pass_color, pass_width in [(shadow_color, 4), (color, 2)]:
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            dx = x2 - x1
            dy = y2 - y1
            length = math.sqrt(dx * dx + dy * dy)
            if length < 1:
                continue
            dash_len = 8
            num_dashes = max(1, int(length / dash_len))
            for d in range(0, num_dashes, 2):
                t0 = d / num_dashes
                t1 = min((d + 1) / num_dashes, 1.0)
                sx = int(x1 + dx * t0)
                sy = int(y1 + dy * t0)
                ex = int(x1 + dx * t1)
                ey = int(y1 + dy * t1)
                draw.line([(sx, sy), (ex, ey)], fill=pass_color, width=pass_width)


def _draw_aircraft_marker(draw, ac, center_lat, center_lon, zoom, vw, vh, units="imperial"):
    """Draw a rotated airplane silhouette marker for an aircraft."""
    px, py = _latlon_to_pixel(ac["lat"], ac["lon"], center_lat, center_lon, zoom, vw, vh)

    if px < -20 or px > vw + 20 or py < -20 or py > vh + 20:
        return

    heading = ac.get("heading")
    color = _get_aircraft_color(ac)

    size = 14
    if heading is not None:
        angle = math.radians(heading)
    else:
        angle = 0

    raw_points = [
        (0, -size),
        (1.5, -size * 0.5),
        (size * 0.9, -size * 0.1),
        (size * 0.9, size * 0.1),
        (1.5, size * 0.15),
        (2, size * 0.6),
        (2, size * 0.8),
        (0.8, size * 0.5),
        (0, size * 0.7),
        (-0.8, size * 0.5),
        (-2, size * 0.8),
        (-2, size * 0.6),
        (-1.5, size * 0.15),
        (-size * 0.9, size * 0.1),
        (-size * 0.9, -size * 0.1),
        (-1.5, -size * 0.5),
    ]

    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    rotated = []
    for rx, ry in raw_points:
        nx = px + rx * cos_a - ry * sin_a
        ny = py + rx * sin_a + ry * cos_a
        rotated.append((nx, ny))

    # Dark outline for contrast against map, then colored fill
    draw.polygon(rotated, fill=color, outline=(0, 0, 0), width=2)

    # Callsign + altitude label
    callsign = ac.get("callsign", "").strip()
    if callsign:
        label_font = get_font("Jost", 16)
        alt_font = get_font("Jost", 14)
        label_x = int(px + size + 5)
        label_y = int(py - 8)

        tw, th = get_text_dimensions(draw, callsign, label_font)

        # Build altitude line
        alt = ac.get("altitude")
        alt_text = None
        alt_tw, alt_th = 0, 0
        if alt == "ground":
            alt_text = "GND"
        elif isinstance(alt, (int, float)):
            alt_text = _format_altitude(int(alt), units)
        if alt_text:
            alt_tw, alt_th = get_text_dimensions(draw, alt_text, alt_font)

        box_w = max(tw, alt_tw)
        box_h = th + (alt_th + 2 if alt_text else 0)
        pad = 3
        draw.rectangle(
            [(label_x - pad, label_y - pad), (label_x + box_w + pad, label_y + box_h + pad)],
            fill=(0, 0, 0, 160) if hasattr(draw, '_image') else (20, 25, 35)
        )
        draw.text((label_x, label_y), callsign, fill=(255, 255, 255), font=label_font)
        if alt_text:
            draw.text((label_x, label_y + th + 2), alt_text, fill=(180, 200, 255), font=alt_font)


def _draw_center_marker(draw, w, h):
    """Draw a crosshair at the center (user's location)."""
    cx, cy = w // 2, h // 2
    size = 8
    color = (255, 80, 80)
    width = 2

    gap = 3
    draw.line([(cx - size, cy), (cx - gap, cy)], fill=color, width=width)
    draw.line([(cx + gap, cy), (cx + size, cy)], fill=color, width=width)
    draw.line([(cx, cy - size), (cx, cy - gap)], fill=color, width=width)
    draw.line([(cx, cy + gap), (cx, cy + size)], fill=color, width=width)


def _draw_range_ring(draw, center_lat, center_lon, radius_nm, zoom, vw, vh):
    """Draw a circle representing the search radius on the map."""
    radius_deg = radius_nm / 60.0

    points = []
    for angle_deg in range(0, 361, 5):
        angle_rad = math.radians(angle_deg)
        dlat = radius_deg * math.cos(angle_rad)
        dlon = radius_deg * math.sin(angle_rad) / math.cos(math.radians(center_lat))

        px, py = _latlon_to_pixel(
            center_lat + dlat, center_lon + dlon,
            center_lat, center_lon, zoom, vw, vh
        )
        points.append((px, py))

    if len(points) > 2:
        draw.line(points, fill=(255, 80, 80, 100), width=1)


# ─────────────────── Coordinate Math ───────────────────

def _latlon_to_pixel(lat, lon, center_lat, center_lon, zoom, vw, vh):
    """Convert lat/lon to viewport pixel coordinates using Web Mercator projection."""
    n = 2 ** zoom

    def to_world(la, lo):
        x = (lo + 180) / 360 * n * TILE_SIZE
        lat_rad = math.radians(la)
        y = (1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * n * TILE_SIZE
        return x, y

    cx, cy = to_world(center_lat, center_lon)
    px, py = to_world(lat, lon)

    return int(vw / 2 + (px - cx)), int(vh / 2 + (py - cy))


def _haversine_nm(lat1, lon1, lat2, lon2):
    """Calculate distance between two points in nautical miles."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_NM * c


# ─────────────────── Unit Formatting ───────────────────

def _format_altitude(alt_ft, units):
    """Format altitude with units."""
    if units == "metric":
        meters = int(alt_ft * 0.3048)
        if meters >= 10000:
            return f"{meters / 1000:.1f}km"
        return f"{meters:,}m"
    else:
        return f"{alt_ft:,}ft"


def _format_speed(speed_kts, units):
    """Format speed with units."""
    if units == "metric":
        kmh = speed_kts * 1.852
        return f"{kmh:.0f}km/h"
    elif units == "imperial":
        mph = speed_kts * 1.15078
        return f"{mph:.0f}mph"
    else:
        return f"{speed_kts:.0f}kts"


def _format_distance(dist_nm, units):
    """Format distance with units."""
    if units == "metric":
        km = dist_nm * 1.852
        return f"{km:.1f}km"
    elif units == "imperial":
        mi = dist_nm * 1.15078
        return f"{mi:.1f}mi"
    else:
        return f"{dist_nm:.1f}nm"


def _convert_distance(dist_nm, units):
    """Convert distance value for display."""
    if units == "metric":
        return f"{dist_nm * 1.852:.0f}"
    elif units == "imperial":
        return f"{dist_nm * 1.15078:.0f}"
    else:
        return str(dist_nm)


def _radius_unit_label(units):
    if units == "metric":
        return "km"
    elif units == "imperial":
        return "mi"
    return "nm"


# ─────────────────── Utilities ───────────────────

def _parse_float(value, default):
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _parse_int(value, default):
    if value is None:
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _find_weather_location(device_config):
    """Try to find location from weather plugin settings."""
    try:
        loop_manager = device_config.get_loop_manager()
        for loop in loop_manager.loops:
            for ref in loop.plugin_order:
                if ref.plugin_id == "weather" and ref.plugin_settings:
                    s = ref.plugin_settings
                    lat = s.get("latitude")
                    lon = s.get("longitude")
                    if lat and lon:
                        return float(lat), float(lon)
                    geo = s.get("geoCoordinates", "")
                    if geo and "," in geo:
                        parts = geo.split(",")
                        return float(parts[0].strip()), float(parts[1].strip())
    except Exception:
        pass
    return None, None
