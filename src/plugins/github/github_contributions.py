import logging
from datetime import datetime, date, timedelta
from PIL import Image, ImageDraw
from utils.app_utils import get_font
from utils.text_utils import get_text_dimensions
from utils.layout_utils import draw_rounded_rect
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

GRAPHQL_QUERY = """
query($username: String!) {
  user(login: $username) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            contributionCount
            date
          }
        }
      }
    }
  }
}
"""

def contributions_generate_image(plugin_instance, settings, device_config):
    dimensions = device_config.get_resolution()
    if device_config.get_config("orientation") == "vertical":
        dimensions = dimensions[::-1]

    api_key = device_config.load_env_key("GITHUB_SECRET")
    if not api_key:
        raise RuntimeError("GitHub API Key not configured.")

    colors = settings.get("contributionColor[]")
    github_username = settings.get("githubUsername")
    if not github_username:
        raise RuntimeError("GitHub username is required.")

    data = fetch_contributions(github_username, api_key)
    grid, month_positions = parse_contributions(data, colors)
    metrics = calculate_metrics(data)

    return _render_pil(dimensions, github_username, grid, month_positions, metrics, settings)

def _render_pil(dimensions, username, grid, month_positions, metrics, settings):
    width, height = dimensions
    bg_color = settings.get("backgroundColor", "#ffffff")
    text_color = settings.get("textColor", "#000000")

    image = Image.new("RGBA", dimensions, bg_color)
    draw = ImageDraw.Draw(image)

    margin = int(width * 0.03)

    # Font sizes
    title_size = int(min(height * 0.08, width * 0.08))
    month_label_size = int(min(height * 0.03, width * 0.03))
    metric_title_size = int(min(height * 0.045, width * 0.04))
    metric_value_size = int(min(height * 0.10, width * 0.10))

    title_font = get_font("Jost", title_size, "bold")
    month_font = get_font("Jost", month_label_size, "bold")
    metric_title_font = get_font("Jost", metric_title_size)
    metric_value_font = get_font("Jost", metric_value_size, "bold")

    y = margin

    # Title
    title_text = f"GitHub/{username}"
    tw = get_text_dimensions(draw, title_text, title_font)[0]
    title_visual_h = int(title_size * 1.15)
    draw.text(((width - tw) // 2, y), title_text, font=title_font, fill=text_color)
    y += title_visual_h + int(height * 0.03)

    # Metrics row
    metric_y = y
    metric_section_width = width - margin * 2
    metric_w = metric_section_width // len(metrics) if metrics else 0
    for i, m in enumerate(metrics):
        mx = margin + i * metric_w + metric_w // 2
        mt_text = m["title"]
        mv_text = str(m["value"])
        mt_w = get_text_dimensions(draw, mt_text, metric_title_font)[0]
        mv_w, mv_h = get_text_dimensions(draw, mv_text, metric_value_font)
        draw.text((mx - mt_w // 2, metric_y), mt_text, font=metric_title_font, fill=text_color)
        mt_h = int(metric_title_size * 1.15)
        draw.text((mx - mv_w // 2, metric_y + mt_h + 2), mv_text, font=metric_value_font, fill=text_color)

    if metrics:
        y = metric_y + int(metric_title_size * 1.15) + \
            int(metric_value_size * 1.15) + int(height * 0.03)

    # Month labels row
    num_weeks = len(grid)
    grid_x_start = margin
    grid_width = width - margin * 2
    month_label_height = get_text_dimensions(draw, "Jan", month_font)[1]

    cell_gap = max(1, int(grid_width / num_weeks * 0.15))
    cell_size = (grid_width - cell_gap * (num_weeks - 1)) // num_weeks if num_weeks > 0 else 1
    # Recalculate to fit exactly
    actual_grid_width = num_weeks * cell_size + (num_weeks - 1) * cell_gap
    grid_x_start = (width - actual_grid_width) // 2

    # Draw month labels
    for mp in month_positions:
        lx = grid_x_start + mp["index"] * (cell_size + cell_gap)
        draw.text((lx, y), mp["name"], font=month_font, fill=text_color)

    y += month_label_height + int(height * 0.01)

    # Contribution grid: 53 cols x 7 rows
    grid_y_start = y
    # Recalculate cell_size to also fit 7 rows
    available_height = height - grid_y_start - margin
    cell_size_h = (available_height - cell_gap * 6) // 7
    cell_size = min(cell_size, cell_size_h)

    for week_idx, week in enumerate(grid):
        for day_idx, day in enumerate(week):
            cx = grid_x_start + week_idx * (cell_size + cell_gap)
            cy = grid_y_start + day_idx * (cell_size + cell_gap)
            color = day.get("color", "#ebedf0")
            radius = max(1, cell_size // 5)
            draw_rounded_rect(draw, (cx, cy, cx + cell_size, cy + cell_size),
                              radius, fill=color)

    return image


# -------------------------
# Helper functions
# -------------------------

def fetch_contributions(username, api_key):
    url = "https://api.github.com/graphql"
    headers = {"Authorization": f"Bearer {api_key}"}
    variables = {"username": username}
    session = get_http_session()
    resp = session.post(url, json={"query": GRAPHQL_QUERY, "variables": variables}, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()

def parse_contributions(data, colors):
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]

    grid = [list(week["contributionDays"]) for week in weeks]
    max_contrib = max(day["contributionCount"] for week in grid for day in week)

    def get_color(count):
        if max_contrib == 0 or count == 0:
            return colors[0]
        level = int((count / max_contrib) * (len(colors) - 1))
        return colors[max(1, level)]

    for week in grid:
        for day in week:
            day["color"] = get_color(day["contributionCount"])

    month_positions = []
    seen_months = set()
    for i, week in enumerate(weeks):
        first_day = week["contributionDays"][0]["date"]
        dt = datetime.strptime(first_day, "%Y-%m-%d")
        month_year = f"{dt.strftime('%b')}-{dt.year}"
        if month_year not in seen_months:
            month_positions.append({"name": dt.strftime("%b"), "index": i})
            seen_months.add(month_year)

    if month_positions:
        month_positions.pop(0)

    return grid, month_positions

def calculate_metrics(data):
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    days = [day for week in weeks for day in week["contributionDays"]]
    days = sorted(days, key=lambda d: d["date"])

    total = sum(day["contributionCount"] for day in days)
    streak, longest_streak, current_streak = 0, 0, 0
    today = date.today()
    yesterday = today - timedelta(days=1)
    in_current_streak = False

    for day in days:
        day_date = date.fromisoformat(day["date"])
        if day["contributionCount"] > 0:
            streak += 1
            longest_streak = max(longest_streak, streak)
            if day_date in (today, yesterday) or in_current_streak:
                current_streak = streak
                in_current_streak = True
        else:
            streak = 0
            in_current_streak = False

    return [
        {"title": "Contributions", "value": total},
        {"title": "Current Streak", "value": current_streak},
        {"title": "Longest Streak", "value": longest_streak},
    ]
