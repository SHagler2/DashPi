import logging
from PIL import Image, ImageDraw
from utils.app_utils import get_font
from utils.text_utils import get_text_dimensions
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

def stars_generate_image(plugin_instance, settings, device_config):
    username = settings.get('githubUsername')
    repository = settings.get('githubRepository')

    dimensions = device_config.get_resolution()
    if device_config.get_config("orientation") == "vertical":
        dimensions = dimensions[::-1]

    github_repository = username + "/" + repository
    if not github_repository:
        raise RuntimeError("GitHub repository is required.")

    try:
        stars = fetch_stars(github_repository)
    except Exception as e:
        logger.error(f"GitHub graphql request failed: {str(e)}")
        raise RuntimeError(f"GitHub request failure, please check logs")

    return _render_pil(dimensions, github_repository, stars, settings)


def _render_pil(dimensions, repository, stars, settings):
    width, height = dimensions
    bg_color = settings.get("backgroundColor", "#ffffff")
    text_color = settings.get("textColor", "#000000")

    image = Image.new("RGBA", dimensions, bg_color)
    draw = ImageDraw.Draw(image)

    title_size = int(min(height * 0.08, width * 0.08))
    count_size = int(min(height * 0.30, width * 0.30))
    label_size = int(min(height * 0.08, width * 0.08))

    title_font = get_font("Jost", title_size, "bold")
    count_font = get_font("Jost", count_size, "bold")
    label_font = get_font("Jost", label_size)

    title_text = f"GitHub/{repository}"
    count_text = str(stars)
    label_text = "Stars"

    title_h = int(title_size * 1.15)
    count_h = int(count_size * 1.15)
    label_h = int(label_size * 1.15)

    spacing = int(height * 0.04)
    total_h = title_h + spacing + count_h + spacing + label_h
    y = (height - total_h) // 2

    # Title centered
    tw = get_text_dimensions(draw, title_text, title_font)[0]
    draw.text(((width - tw) // 2, y), title_text, font=title_font, fill=text_color)
    y += title_h + spacing

    # Star count centered
    cw = get_text_dimensions(draw, count_text, count_font)[0]
    draw.text(((width - cw) // 2, y), count_text, font=count_font, fill=text_color)
    y += count_h + spacing

    # "Stars" label centered
    lw = get_text_dimensions(draw, label_text, label_font)[0]
    draw.text(((width - lw) // 2, y), label_text, font=label_font, fill=text_color)

    return image

def fetch_stars(github_repository):
    url = f"https://api.github.com/repos/{github_repository}"
    headers = {"Accept": "application/json"}

    session = get_http_session()
    response = session.get(url, headers=headers, timeout=30)
    if response.status_code == 200:
        data = response.json()
    else:
        logger.error(f"GitHub Stars Plugin: Error: {response.status_code} - {response.text}")
        data = {"stargazers_count": 0}

    return data['stargazers_count']

