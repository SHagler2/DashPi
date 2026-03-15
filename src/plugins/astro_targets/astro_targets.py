"""Astro Targets plugin — shows tonight's best DSO imaging targets for backyard astrophotography."""

import json
import logging
import math
import os
from datetime import datetime, timedelta

from PIL import Image, ImageDraw
from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import get_font
from utils.text_utils import get_text_dimensions, truncate_text

logger = logging.getLogger(__name__)

# Equipment profiles: name, FOV width/height in degrees
EQUIPMENT_PROFILES = [
    {"name": "ZS61 + ASI2600MM", "fov_w": 3.74, "fov_h": 2.50},
    {"name": "SeeStar S50", "fov_w": 2.0, "fov_h": 1.3},
    {"name": "FF107 + ASI2600MM", "fov_w": 1.93, "fov_h": 1.29},
    {"name": "ZS61 + ASI174MM", "fov_w": 1.80, "fov_h": 1.13},
    {"name": "FF107 + ASI174MM", "fov_w": 0.93, "fov_h": 0.58},
]

# Default horizon profile (Steven's backyard)
DEFAULT_HORIZON = [
    {"az": 0, "alt": 50},
    {"az": 45, "alt": 50},
    {"az": 90, "alt": 45},
    {"az": 135, "alt": 20},
    {"az": 180, "alt": 15},
    {"az": 225, "alt": 15},
    {"az": 270, "alt": 20},
    {"az": 315, "alt": 50},
]

# DSO type display info: label, icon color
TYPE_INFO = {
    "emission_nebula": ("Emission Neb.", "#e74c3c"),
    "reflection_nebula": ("Reflection Neb.", "#5dade2"),
    "planetary_nebula": ("Planetary Neb.", "#2ecc71"),
    "dark_nebula": ("Dark Nebula", "#7f8c8d"),
    "supernova_remnant": ("SNR", "#e67e22"),
    "galaxy": ("Galaxy", "#f1c40f"),
    "galaxy_group": ("Galaxy Group", "#f39c12"),
    "open_cluster": ("Open Cluster", "#3498db"),
    "globular_cluster": ("Globular Cluster", "#9b59b6"),
}

# Moon phase names
MOON_PHASES = [
    "New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
    "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent",
]

# Cache for catalog and ephemeris
_catalog_cache = None
_ephemeris_cache = None


def _load_catalog(plugin_dir):
    """Load the curated DSO catalog from resources/targets.json."""
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache
    catalog_path = os.path.join(plugin_dir, "resources", "targets.json")
    with open(catalog_path, "r") as f:
        _catalog_cache = json.load(f)
    logger.info("Loaded %d DSO targets from catalog", len(_catalog_cache))
    return _catalog_cache


def _get_ephemeris(plugin_dir):
    """Load or download the de421.bsp ephemeris file."""
    global _ephemeris_cache
    if _ephemeris_cache is not None:
        return _ephemeris_cache
    from skyfield.api import Loader
    resources_dir = os.path.join(plugin_dir, "resources")
    load = Loader(resources_dir)
    _ephemeris_cache = load("de421.bsp")
    return _ephemeris_cache


def _get_horizon_alt(horizon_profile, azimuth):
    """Interpolate minimum altitude from horizon profile at given azimuth (0-360)."""
    az = azimuth % 360
    points = sorted(horizon_profile, key=lambda p: p["az"])
    # Wrap around: add copy of first point at 360
    wrapped = points + [{"az": points[0]["az"] + 360, "alt": points[0]["alt"]}]

    for i in range(len(wrapped) - 1):
        a1, a2 = wrapped[i]["az"], wrapped[i + 1]["az"]
        if a1 <= az <= a2:
            frac = (az - a1) / (a2 - a1) if a2 != a1 else 0
            return wrapped[i]["alt"] + frac * (wrapped[i + 1]["alt"] - wrapped[i]["alt"])
        # Handle wrap for az < first point
        if i == 0 and az < a1:
            prev = points[-1]
            a0 = prev["az"] - 360
            frac = (az - a0) / (a1 - a0) if a1 != a0 else 0
            return prev["alt"] + frac * (points[0]["alt"] - prev["alt"])

    return points[-1]["alt"]


def _compute_tonight_window(topos, eph, ts, date):
    """Compute astronomical twilight dusk (evening) to dawn (morning) using Skyfield."""
    from skyfield import almanac

    # Search from noon today to noon tomorrow to find dusk/dawn
    t0 = ts.utc(date.year, date.month, date.day, 12)
    t1 = ts.utc(date.year, date.month, date.day + 1, 12)

    # Astronomical twilight: sun at -18 degrees
    f = almanac.dark_twilight_day(eph, topos)
    times, events = almanac.find_discrete(t0, t1, f)

    # dark_twilight_day returns events:
    # 0 = night (sun below -18), 1 = astronomical twilight, 2 = nautical, 3 = civil, 4 = day
    # We want: transition TO 0 (dusk) and transition FROM 0 (dawn)
    dusk_idx = None
    dawn_idx = None
    for i, e in enumerate(events):
        if e == 0 and dusk_idx is None:
            dusk_idx = i
        if dusk_idx is not None and e > 0 and dawn_idx is None:
            dawn_idx = i

    if dusk_idx is None or dawn_idx is None:
        # Fallback: use 9pm to 5am UTC (rough)
        logger.warning("Could not compute twilight times, using fallback")
        return ts.utc(date.year, date.month, date.day + 1, 1), ts.utc(date.year, date.month, date.day + 1, 11)

    return times[dusk_idx], times[dawn_idx]


def _compute_moon_info(observer_pos, eph, ts, t_mid):
    """Compute moon illumination percentage and phase name.

    observer_pos should be earth + topos (a Skyfield VectorSum).
    """
    from skyfield import almanac
    moon = eph["moon"]

    # Moon phase angle (0-360)
    phase_angle = almanac.moon_phase(eph, t_mid).degrees
    # Illumination from phase angle
    illumination = (1 - math.cos(math.radians(phase_angle))) / 2 * 100

    # Phase name
    idx = int((phase_angle + 22.5) / 45) % 8
    phase_name = MOON_PHASES[idx]

    # Moon position at mid-observation
    apparent = observer_pos.at(t_mid).observe(moon).apparent()
    moon_alt, moon_az, _ = apparent.altaz()

    return {
        "illumination": illumination,
        "phase_name": phase_name,
        "alt": moon_alt.degrees,
        "az": moon_az.degrees,
    }


def _build_time_array(ts, dusk, dawn):
    """Build a vectorized Skyfield time array for the night at 30-min intervals."""
    import numpy as np
    dusk_tt = dusk.tt
    dawn_tt = dawn.tt
    interval = 30 / (24 * 60)  # 30 min in days
    num_steps = max(1, int((dawn_tt - dusk_tt) / interval))
    jd_array = np.array([dusk_tt + i * interval for i in range(num_steps + 1)])
    return ts.tt(jd=jd_array), num_steps + 1


def _compute_all_visibilities(catalog, observer_pos, t_array, num_steps, horizon_profile):
    """Compute visibility for all targets using vectorized Skyfield calls.

    Returns dict of target_id -> {peak_alt, total_minutes} for visible targets.
    """
    from skyfield.api import Star

    # Pre-compute observer position at all times (vectorized)
    obs_at = observer_pos.at(t_array)

    results = {}
    for target in catalog:
        star = Star(ra_hours=target["ra_hours"], dec_degrees=target["dec_degrees"])

        # Vectorized observe: computes all time steps at once
        apparent = obs_at.observe(star).apparent()
        alt, az, _ = apparent.altaz()

        # alt.degrees and az.degrees are now numpy arrays
        alt_arr = alt.degrees
        az_arr = az.degrees

        peak_alt = -90.0
        visible_minutes = 0

        for i in range(num_steps):
            min_alt = _get_horizon_alt(horizon_profile, float(az_arr[i]))
            if alt_arr[i] > min_alt:
                visible_minutes += 30
                if alt_arr[i] > peak_alt:
                    peak_alt = float(alt_arr[i])

        if visible_minutes > 0:
            results[target["id"]] = {
                "peak_alt": peak_alt,
                "total_minutes": visible_minutes,
            }

    return results


def _best_equipment(target, equipment_profiles):
    """Find the best equipment setup for a target based on FOV matching.

    Object should fill 30-80% of the shorter FOV axis for optimal framing.
    """
    size_deg = target.get("size_arcmin", 10) / 60.0
    best_name = None
    best_score = -1

    for profile in equipment_profiles:
        shorter_fov = min(profile["fov_w"], profile["fov_h"])
        fill_fraction = size_deg / shorter_fov

        # Ideal fill: 30-80% of shorter axis. Score peaks at 50%.
        if 0.1 <= fill_fraction <= 1.5:
            # Distance from ideal 0.5 fill
            score = 1.0 - abs(fill_fraction - 0.5) / 0.5
            if score > best_score:
                best_score = score
                best_name = profile["name"]

    if best_name is None:
        # Default to widest FOV if nothing matches well
        best_name = max(equipment_profiles, key=lambda p: p["fov_w"])["name"]

    return best_name


def _rank_targets(visible_targets):
    """Rank targets by combined score: peak altitude, visibility time, brightness."""
    for t in visible_targets:
        vis = t["visibility"]
        # Normalize components (0-1 scale)
        alt_score = vis["peak_alt"] / 90.0
        time_score = min(vis["total_minutes"] / 360, 1.0)  # Cap at 6 hours
        # Brighter = better (lower magnitude = brighter)
        mag = t.get("magnitude") or 10
        mag_score = max(0, 1.0 - (mag - 3) / 10.0)

        t["score"] = alt_score * 0.4 + time_score * 0.4 + mag_score * 0.2

    return sorted(visible_targets, key=lambda t: t["score"], reverse=True)


def _format_duration(minutes):
    """Format minutes as 'Xh Ym'."""
    h = int(minutes // 60)
    m = int(minutes % 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def _draw_type_icon(draw, x, y, size, obj_type, color):
    """Draw a simple icon representing the DSO type."""
    cx, cy = x + size // 2, y + size // 2
    r = size // 2 - 2

    if "nebula" in obj_type or obj_type == "supernova_remnant":
        # Nebula: filled circle with glow effect
        for i in range(3):
            opacity = 60 + i * 40
            ri = r - i * 2
            if ri > 0:
                draw.ellipse([cx - ri, cy - ri, cx + ri, cy + ri],
                             fill=color + hex(opacity)[2:].zfill(2))
        draw.ellipse([cx - r // 3, cy - r // 3, cx + r // 3, cy + r // 3], fill=color)

    elif "galaxy" in obj_type:
        # Galaxy: tilted ellipse
        draw.ellipse([cx - r, cy - r // 2, cx + r, cy + r // 2], fill=color + "80")
        draw.ellipse([cx - r // 2, cy - r // 4, cx + r // 2, cy + r // 4], fill=color)

    elif "cluster" in obj_type:
        # Cluster: scattered dots
        import random
        rng = random.Random(42)  # Deterministic
        for _ in range(7):
            dx = rng.randint(-r + 2, r - 2)
            dy = rng.randint(-r + 2, r - 2)
            dot_r = rng.randint(1, 3)
            draw.ellipse([cx + dx - dot_r, cy + dy - dot_r,
                          cx + dx + dot_r, cy + dy + dot_r], fill=color)
    else:
        # Default: simple circle
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + "80")


def _draw_moon_phase_icon(draw, x, y, size, illumination, phase_name):
    """Draw a moon phase icon."""
    cx, cy = x + size // 2, y + size // 2
    r = size // 2 - 1

    # Draw full moon circle (dark)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#444444")

    # Draw illuminated portion
    frac = illumination / 100.0
    if "New" in phase_name:
        return  # All dark
    if "Full" in phase_name:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#e8e8e8")
        return

    # Approximate illuminated portion with overlapping ellipses
    waning = "Waning" in phase_name
    # First draw the bright half
    if waning:
        # Left side is lit
        draw.pieslice([cx - r, cy - r, cx + r, cy + r], 90, 270, fill="#e8e8e8")
    else:
        # Right side is lit
        draw.pieslice([cx - r, cy - r, cx + r, cy + r], 270, 90, fill="#e8e8e8")

    # Then mask with an ellipse for the terminator
    if frac < 0.5:
        # Less than half: carve out from the lit side
        term_w = int(r * (1 - 2 * frac))
        if waning:
            draw.ellipse([cx - term_w, cy - r, cx + term_w, cy + r], fill="#444444")
        else:
            draw.ellipse([cx - term_w, cy - r, cx + term_w, cy + r], fill="#444444")
    else:
        # More than half: extend into the dark side
        term_w = int(r * (2 * frac - 1))
        if waning:
            draw.ellipse([cx - term_w, cy - r, cx + term_w, cy + r], fill="#e8e8e8")
        else:
            draw.ellipse([cx - term_w, cy - r, cx + term_w, cy + r], fill="#e8e8e8")


class AstroTargets(BasePlugin):
    """Shows tonight's best deep sky imaging targets filtered by sky window and equipment."""

    def __init__(self, config, **dependencies):
        super().__init__(config, **dependencies)
        self._cached_results = None
        self._cache_date = None

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = False
        return template_params

    def generate_image(self, settings, device_config):
        from skyfield.api import Loader, wgs84

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        timezone_name = device_config.get_config("timezone", default="America/Chicago")

        # Parse settings
        lat = float(settings.get("latitude", "32.7767"))
        lon = float(settings.get("longitude", "-96.7970"))
        max_targets = int(settings.get("maxTargets", "4"))
        horizon_json = settings.get("horizonProfile", "")
        enabled_types = self._get_enabled_types(settings)
        enabled_equipment = self._get_enabled_equipment(settings)

        # Parse horizon profile
        horizon_profile = DEFAULT_HORIZON
        if horizon_json:
            try:
                horizon_profile = json.loads(horizon_json)
            except (json.JSONDecodeError, TypeError):
                pass

        # Equipment profiles filtered by user selection
        equipment = [p for p in EQUIPMENT_PROFILES if p["name"] in enabled_equipment]
        if not equipment:
            equipment = EQUIPMENT_PROFILES

        # Compute targets
        plugin_dir = self.get_plugin_dir()
        catalog = _load_catalog(plugin_dir)
        eph = _get_ephemeris(plugin_dir)

        # Setup Skyfield
        resources_dir = os.path.join(plugin_dir, "resources")
        load = Loader(resources_dir)
        ts = load.timescale()
        topos = wgs84.latlon(lat, lon)
        earth = eph["earth"]
        observer_pos = earth + topos  # For observe() calls

        # Get current date in user's timezone
        import pytz
        tz = pytz.timezone(timezone_name)
        now = datetime.now(tz)

        # Use today's date for "tonight" (if before noon, use yesterday)
        if now.hour < 12:
            date = (now - timedelta(days=1)).date()
        else:
            date = now.date()

        # Check cache
        cache_key = (date, lat, lon)
        if self._cached_results is not None and self._cache_date == cache_key:
            ranked, moon_info = self._cached_results
        else:
            # Compute tonight's window
            dusk, dawn = _compute_tonight_window(topos, eph, ts, date)

            # Moon info at mid-night
            t_mid = ts.tt(jd=(dusk.tt + dawn.tt) / 2)
            moon_info = _compute_moon_info(observer_pos, eph, ts, t_mid)

            # Filter catalog by enabled types
            filtered = [t for t in catalog if t.get("type", "") in enabled_types]

            # Compute visibility for all targets (vectorized)
            t_array, num_steps = _build_time_array(ts, dusk, dawn)
            vis_map = _compute_all_visibilities(filtered, observer_pos, t_array, num_steps, horizon_profile)

            visible = []
            for target in filtered:
                vis = vis_map.get(target["id"])
                if vis is not None:
                    entry = dict(target)
                    entry["visibility"] = vis
                    entry["equipment"] = _best_equipment(target, equipment)
                    visible.append(entry)

            ranked = _rank_targets(visible)
            self._cached_results = (ranked, moon_info)
            self._cache_date = cache_key
            logger.info("Computed %d visible targets for %s (of %d filtered)", len(ranked), date, len(filtered))

        # Render
        top_targets = ranked[:max_targets]
        return self._render_pil(dimensions, top_targets, moon_info, date, settings)

    def _get_enabled_types(self, settings):
        """Get set of enabled DSO types from settings."""
        all_types = set(TYPE_INFO.keys())
        enabled = set()
        for t in all_types:
            key = f"type_{t}"
            if settings.get(key, "true") in ("true", True, "on"):
                enabled.add(t)
        return enabled if enabled else all_types

    def _get_enabled_equipment(self, settings):
        """Get set of enabled equipment profile names from settings."""
        all_names = {p["name"] for p in EQUIPMENT_PROFILES}
        enabled = set()
        for p in EQUIPMENT_PROFILES:
            key = f"equip_{p['name'].replace(' ', '_').replace('+', '')}"
            if settings.get(key, "true") in ("true", True, "on"):
                enabled.add(p["name"])
        return enabled if enabled else all_names

    def _render_pil(self, dimensions, targets, moon_info, date, settings):
        """Render the target list as a dark-themed PIL image."""
        width, height = dimensions
        is_vertical = height > width

        bg_color = settings.get("backgroundColor", "#1a1a2e")
        text_color = settings.get("textColor", "#e8e8e8")
        accent_color = "#4a9eff"
        dim_color = "#888888"

        image = Image.new("RGBA", dimensions, bg_color)
        draw = ImageDraw.Draw(image)

        # Margins and spacing
        margin = int(width * 0.03)
        header_height = int(height * 0.13)

        # -- Header --
        title_size = int(min(width * 0.035, height * 0.065))
        title_font = get_font("Jost", title_size, "bold")
        date_font = get_font("Jost", int(title_size * 0.65), "normal")
        moon_font = get_font("Jost", int(title_size * 0.6), "normal")

        # Title
        draw.text((margin, margin), "Tonight's Targets", font=title_font, fill=text_color)

        # Date subtitle
        from calendar import month_abbr
        date_str = f"{month_abbr[date.month]} {date.day}, {date.year}"
        date_y = margin + int(title_size * 1.15)
        draw.text((margin, date_y), date_str, font=date_font, fill=dim_color)

        # Moon info (right-aligned)
        moon_text = f"{moon_info['illumination']:.0f}% {moon_info['phase_name']}"
        moon_w = get_text_dimensions(draw, moon_text, moon_font)[0]
        moon_x = width - margin - moon_w
        moon_icon_size = int(title_size * 0.7)
        _draw_moon_phase_icon(draw, moon_x - moon_icon_size - 8, margin + 2,
                              moon_icon_size, moon_info["illumination"], moon_info["phase_name"])
        draw.text((moon_x, margin + 4), moon_text, font=moon_font, fill=dim_color)

        # Moon altitude info
        if moon_info["alt"] > 0:
            moon_alt_text = f"Moon alt: {moon_info['alt']:.0f} deg"
            draw.text((moon_x, margin + int(title_size * 0.8)), moon_alt_text,
                      font=get_font("Jost", int(title_size * 0.45), "normal"), fill=dim_color)

        # Separator line
        sep_y = margin + header_height - 4
        draw.line([(margin, sep_y), (width - margin, sep_y)], fill="#333355", width=1)

        # -- Target cards --
        if not targets:
            no_targets_font = get_font("Jost", int(title_size * 0.9), "normal")
            msg = "No targets visible tonight"
            msg_w = get_text_dimensions(draw, msg, no_targets_font)[0]
            draw.text(((width - msg_w) // 2, height // 2 - title_size),
                      msg, font=no_targets_font, fill=dim_color)
            return image

        card_area_top = sep_y + 8
        card_area_height = height - card_area_top - margin
        num_targets = len(targets)
        card_spacing = 6
        # Card height: divide available space evenly, capped at reasonable max
        card_height = min(
            int((card_area_height - (num_targets - 1) * card_spacing) / num_targets),
            int(height * 0.20)
        )

        # Font sizes for cards
        name_size = int(min(width * 0.028, card_height * 0.35))
        detail_size = int(name_size * 0.72)
        id_size = int(name_size * 0.85)
        icon_size = min(int(card_height * 0.55), int(name_size * 2.5))

        name_font = get_font("Jost", name_size, "bold")
        detail_font = get_font("Jost", detail_size, "normal")
        id_font = get_font("Jost", id_size, "bold")

        for i, target in enumerate(targets):
            card_y = card_area_top + i * (card_height + card_spacing)
            if card_y + card_height > height - margin:
                break

            # Card background
            card_rect = [margin, card_y, width - margin, card_y + card_height]
            draw.rounded_rectangle(card_rect, radius=8, fill="#16213e")

            # Type icon
            type_info = TYPE_INFO.get(target.get("type", ""), ("", "#888888"))
            icon_x = margin + 12
            icon_y = card_y + (card_height - icon_size) // 2
            _draw_type_icon(draw, icon_x, icon_y, icon_size, target.get("type", ""), type_info[1])

            # Target ID and name
            text_x = icon_x + icon_size + 14
            line1_y = card_y + int(card_height * 0.15)

            target_id = target.get("id", "")
            target_name = target.get("name", "") or target.get("constellation", "")

            # ID in accent color
            draw.text((text_x, line1_y), target_id, font=id_font, fill=accent_color)
            id_w = get_text_dimensions(draw, target_id, id_font)[0]

            # Separator dash and name
            separator = " -- "
            draw.text((text_x + id_w, line1_y), separator, font=id_font, fill=dim_color)
            sep_w = get_text_dimensions(draw, separator, id_font)[0]

            # Truncate name if needed
            max_name_w = (width - margin) - (text_x + id_w + sep_w) - margin
            display_name = truncate_text(draw, target_name, name_font, max_name_w)
            draw.text((text_x + id_w + sep_w, line1_y), display_name, font=name_font, fill=text_color)

            # Second line: equipment + visibility duration + type
            line2_y = line1_y + int(name_size * 1.25)
            vis = target.get("visibility", {})
            equip = target.get("equipment", "")
            duration = _format_duration(vis.get("total_minutes", 0))
            peak = f"peak {vis.get('peak_alt', 0):.0f} deg"
            type_label = type_info[0]

            detail_text = f"{equip}  |  {duration} visible  |  {peak}"
            max_detail_w = (width - margin) - text_x - margin
            detail_text = truncate_text(draw, detail_text, detail_font, max_detail_w)
            draw.text((text_x, line2_y), detail_text, font=detail_font, fill=dim_color)

            # Type label (right-aligned)
            type_w = get_text_dimensions(draw, type_label, detail_font)[0]
            type_x = width - margin - 12 - type_w
            if type_x > text_x + get_text_dimensions(draw, detail_text, detail_font)[0] + 10:
                draw.text((type_x, line2_y), type_label, font=detail_font, fill=type_info[1])

        return image
