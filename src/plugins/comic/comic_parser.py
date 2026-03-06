import html
import re
from utils.http_client import get_http_session

FEED_TIMEOUT = 15  # Seconds before giving up on comic feed fetch


def _safe_search(pattern, text, default=""):
    """Safely extract a regex group, returning default if no match."""
    match = re.search(pattern, text)
    return match.group(1) if match else default


COMICS = {
    "XKCD": {
        "feed": "https://xkcd.com/atom.xml",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: _safe_search(r'<img[^>]+src=["\']([^"\']+)["\']', element),
        "title": lambda feed: feed.entries[0].title,
        "caption": lambda element: _safe_search(r'<img[^>]+alt=["\']([^"\']+)["\']', element),
    },
    "Cyanide & Happiness": {
        "feed": "https://explosm-1311.appspot.com/",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: _safe_search(r'<img[^>]+src=["\']([^"\']+)["\']', element),
        "title": lambda feed: feed.entries[0].title.split(" - ")[-1].strip(),
        "caption": lambda element: "",
    },
    "Saturday Morning Breakfast Cereal": {
        "feed": "https://www.smbc-comics.com/comic/rss",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: _safe_search(r'<img[^>]+src=["\']([^"\']+)["\']', element),
        "title": lambda feed: feed.entries[0].title.split("-")[-1].strip(),
        "caption": lambda element: _safe_search(r'Hovertext:<br />(.*?)</p>', element),
    },
    "The Perry Bible Fellowship": {
        "feed": "https://pbfcomics.com/feed/",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: _safe_search(r'<img[^>]+src=["\']([^"\']+)["\']', element),
        "title": lambda feed: feed.entries[0].title,
        "caption": lambda element: _safe_search(r'<img[^>]+alt=["\']([^"\']+)["\']', element),
    },
    "Questionable Content": {
        "feed": "https://www.questionablecontent.net/QCRSS.xml",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: _safe_search(r'<img[^>]+src=["\']([^"\']+)["\']', element),
        "title": lambda feed: feed.entries[0].title,
        "caption": lambda element: "",
    },
    "Poorly Drawn Lines": {
        "feed": "https://poorlydrawnlines.com/feed/",
        "element": lambda feed: feed.entries[0].get('content', [{}])[0].get('value', ''),
        "url": lambda element: _safe_search(r'<img[^>]+src=["\']([^"\']+)["\']', element),
        "title": lambda feed: feed.entries[0].title,
        "caption": lambda element: "",
    },
    "Dinosaur Comics": {
        "feed": "https://www.qwantz.com/rssfeed.php",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: _safe_search(r'<img[^>]+src=["\']([^"\']+)["\']', element),
        "title": lambda feed: feed.entries[0].title,
        "caption": lambda element: _safe_search(r'title="(.*?)" />', element.replace('\n', '')),
    },
    "webcomic name": {
        "feed": "https://webcomicname.com/rss",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: _safe_search(r'<img[^>]+src=["\']([^"\']+)["\']', element),
        "title": lambda feed: "",
        "caption": lambda element: "",
    },
}


def get_panel(comic_name):
    import feedparser

    # Fetch feed via shared HTTP session (with timeout) then parse content
    feed_url = COMICS[comic_name]["feed"]
    session = get_http_session()
    resp = session.get(feed_url, timeout=FEED_TIMEOUT)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)

    try:
        element = COMICS[comic_name]["element"](feed)
    except (IndexError, KeyError):
        raise RuntimeError("Failed to retrieve latest comic.")

    image_url = COMICS[comic_name]["url"](element)
    if not image_url:
        raise RuntimeError("Could not extract comic image URL from feed.")

    try:
        title = html.unescape(COMICS[comic_name]["title"](feed))
    except (IndexError, KeyError, AttributeError):
        title = ""

    try:
        caption = html.unescape(COMICS[comic_name]["caption"](element))
    except (IndexError, KeyError, AttributeError):
        caption = ""

    return {
        "image_url": image_url,
        "title": title,
        "caption": caption,
    }
