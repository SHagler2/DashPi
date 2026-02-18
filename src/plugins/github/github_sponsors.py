import logging
from PIL import Image, ImageDraw
from utils.app_utils import get_font
from utils.text_utils import get_text_dimensions
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

GRAPHQL_QUERY = """
query($username: String!) {
  user(login: $username) {
    sponsorshipsAsMaintainer(first: 100) {
      totalCount
      nodes {
        createdAt
        sponsorEntity {
          ... on User {
            login
            name
          }
          ... on Organization {
            login
            name
          }
        }
        tier {
          name
          monthlyPriceInCents
        }
      }
    }
    estimatedNextSponsorsPayoutInCents
  }
}
"""

def sponsors_generate_image(plugin_instance, settings, device_config):
    dimensions = device_config.get_resolution()
    if device_config.get_config("orientation") == "vertical":
        dimensions = dimensions[::-1]

    api_key = device_config.load_env_key("GITHUB_SECRET")
    if not api_key:
        raise RuntimeError("GitHub API Key not configured.")

    github_username = settings.get("githubUsername")
    if not github_username:
        raise RuntimeError("GitHub username is required.")

    data = fetch_sponsorships(github_username, api_key)
    total_per_month = calculate_monthly_total(data)

    return _render_pil(dimensions, github_username, total_per_month, settings)

def _render_pil(dimensions, username, total_per_month, settings):
    width, height = dimensions
    bg_color = settings.get("backgroundColor", "#ffffff")
    text_color = settings.get("textColor", "#000000")

    image = Image.new("RGBA", dimensions, bg_color)
    draw = ImageDraw.Draw(image)

    title_size = int(min(height * 0.08, width * 0.08))
    amount_size = int(min(height * 0.25, width * 0.25))
    label_size = int(min(height * 0.05, width * 0.05))

    title_font = get_font("Jost", title_size, "bold")
    amount_font = get_font("Jost", amount_size, "bold")
    label_font = get_font("Jost", label_size)

    # Elements to stack vertically
    title_text = f"GitHub/{username}"
    amount_text = f"${total_per_month}"
    label_text = "Earnings this month"

    title_h = int(title_size * 1.15)
    amount_h = int(amount_size * 1.15)
    label_h = int(label_size * 1.15)

    spacing = int(height * 0.04)
    total_h = title_h + spacing + amount_h + spacing + label_h
    y = (height - total_h) // 2

    for text, font, h in [(title_text, title_font, title_h),
                           (amount_text, amount_font, amount_h),
                           (label_text, label_font, label_h)]:
        tw = get_text_dimensions(draw, text, font)[0]
        draw.text(((width - tw) // 2, y), text, font=font, fill=text_color)
        y += h + spacing

    return image


# -------------------------
# Helper functions
# -------------------------

def fetch_sponsorships(username, api_key):
    url = "https://api.github.com/graphql"
    headers = {"Authorization": f"Bearer {api_key}"}
    variables = {"username": username}

    session = get_http_session()
    resp = session.post(url, json={"query": GRAPHQL_QUERY, "variables": variables}, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        raise RuntimeError(f"GitHub API returned errors: {data['errors']}")

    logger.debug(f"Fetched sponsor data for {username}: {data}")
    return data

def calculate_monthly_total(data) -> int:
    sponsorships = data['data']['user']['sponsorshipsAsMaintainer']['nodes']
    total_per_month = sum(s['tier']['monthlyPriceInCents'] / 100 for s in sponsorships)
    return int(total_per_month)
