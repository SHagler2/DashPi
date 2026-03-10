"""Stocks plugin — displays a stock ticker dashboard with price charts."""

from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw
from utils.app_utils import get_font
from utils.text_utils import get_text_dimensions, truncate_text
from utils.layout_utils import calculate_grid
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Timeout for yfinance API calls (seconds)
YFINANCE_TIMEOUT = 15
# Cache TTL: shorter when market open, longer when closed
CACHE_TTL_MARKET_OPEN = 60
CACHE_TTL_MARKET_CLOSED = 3600

# User-selectable font size multipliers applied to all text in the plugin
FONT_SIZES = {
    "x-small": 0.6,
    "small": 0.8,
    "normal": 1,
    "large": 1.25,
    "x-large": 1.5
}

# Additional scale factors applied per stock count to prevent text overflow in small cells
COUNT_SCALES = {1: 1.7, 2: 1.35, 3: 1.2, 4: 1.15, 5: 0.9, 6: 0.85}
# Grid column counts by number of stocks (max 6)
GRID_COLUMNS = {1: 1, 2: 2, 3: 3, 4: 2, 5: 3, 6: 3}


def format_large_number(num):
    """Format large numbers with K, M, B, T suffixes."""
    if num is None:
        return "N/A"
    for threshold, suffix in [(1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")]:
        if num >= threshold:
            return f"{num / threshold:.2f}{suffix}"
    return str(num)


def format_price(value):
    """Format a price value or return N/A."""
    return f"${value:,.2f}" if value is not None else "N/A"


def is_market_open():
    """Check if US stock market (NYSE/NASDAQ) is currently open.

    Open Monday-Friday, 9:30 AM - 4:00 PM Eastern Time.
    Does not account for market holidays.
    """
    now_et = datetime.now(ZoneInfo("America/New_York"))
    # Weekday: 0=Monday, 6=Sunday
    if now_et.weekday() >= 5:
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et < market_close


class Stocks(BasePlugin):
    """Stock ticker dashboard plugin using yfinance.

    Displays up to 6 stock tickers in a responsive grid layout. Each card shows
    the symbol, company name, current price, daily change (colored green/red),
    volume, and day high/low. Supports configurable font sizes and auto-refresh.
    """

    @staticmethod
    def get_loop_weight(settings):
        """Reduce selection weight when market is closed, if user enabled the option."""
        if settings.get('reduceWhenClosed') == 'true' and not is_market_open():
            return 0.2
        return 1.0

    def __init__(self, config, **dependencies):
        super().__init__(config, **dependencies)
        self._stocks_cache = None
        self._stocks_cache_time = 0
        self._stocks_cache_tickers = None

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = False
        template_params['hide_refresh_interval'] = True
        return template_params

    def generate_image(self, settings, device_config):
        """Fetch stock data via yfinance and render ticker cards in a grid layout."""
        title = settings.get("title", "Stock Prices")
        tickers_input = settings.get("tickers", "")

        # Get saved tickers from device config
        saved_tickers_raw = device_config.get_config("stocks_saved_tickers", default=[])
        # Extract symbols from saved tickers (handle both old string format and new dict format)
        saved_tickers = [t["symbol"] if isinstance(t, dict) else t for t in saved_tickers_raw]

        # Parse comma-separated tickers from input (if any)
        input_tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()] if tickers_input else []

        # Use input tickers if provided, otherwise fall back to saved tickers
        tickers = input_tickers if input_tickers else saved_tickers

        if not tickers:
            raise RuntimeError("No tickers configured. Add tickers in the plugin settings.")

        # Fetch stock data (limit to 6 tickers)
        stocks_data = self.fetch_stock_data(tickers[:6])

        if not stocks_data:
            raise RuntimeError("Could not fetch data for any of the provided ticker symbols.")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        stock_count = len(stocks_data)
        columns = GRID_COLUMNS.get(stock_count, 3)
        rows = (stock_count + columns - 1) // columns
        auto_refresh = settings.get('autoRefresh', '0')
        try:
            auto_refresh_mins = int(auto_refresh)
        except (ValueError, TypeError):
            auto_refresh_mins = 0

        font_scale = FONT_SIZES.get(settings.get('fontSize', 'normal'), 1)
        count_scale = COUNT_SCALES.get(stock_count, 0.65)
        tz_str = device_config.get_config("timezone", default="UTC")
        try:
            last_updated = datetime.now(ZoneInfo(tz_str)).strftime("%I:%M %p")
        except Exception:
            logger.warning(f"Invalid timezone '{tz_str}', falling back to UTC")
            last_updated = datetime.now(ZoneInfo("UTC")).strftime("%I:%M %p")
        market_open = is_market_open()

        return self._render_pil(dimensions, title, stocks_data, columns, rows,
                                last_updated, auto_refresh_mins,
                                font_scale * count_scale, font_scale, settings, market_open)

    def _render_pil(self, dimensions, title, stocks, columns, rows,
                    last_updated, auto_refresh_mins, scale, footer_scale, settings,
                    market_open=True):
        """Render stock cards in a grid layout as a PIL Image.

        Each card contains: symbol, price (stacked or side-by-side depending on
        width), company name, daily change with color coding, and volume/high/low
        details. Footer shows refresh interval and last update time.

        Args:
            dimensions: (width, height) tuple for the output image.
            title: Header text (e.g. "Stock Prices").
            stocks: List of stock data dicts from fetch_stock_data().
            columns: Number of grid columns.
            rows: Number of grid rows.
            last_updated: Formatted timestamp string for the footer.
            auto_refresh_mins: Refresh interval in minutes (0 = manual).
            scale: Combined font_size * count_scale multiplier for card content.
            footer_scale: Font size multiplier for footer (without count_scale,
                          so the footer stays readable regardless of stock count).
            settings: Plugin settings dict (colors, etc.).

        Returns:
            PIL Image (RGBA) ready for display.
        """
        width, height = dimensions
        bg_color = settings.get("backgroundColor", "#ffffff")
        text_color = settings.get("textColor", "#000000")

        dark_mode = settings.get("darkMode") in ("on", True)
        if dark_mode:
            bg_color = "#1a1a1a"
            text_color = "#e0e0e0"
            positive_color = "#00c853"
            negative_color = "#ff5252"
        else:
            positive_color = "#006400"
            negative_color = "#8B0000"

        image = Image.new("RGBA", dimensions, bg_color)
        draw = ImageDraw.Draw(image)

        margin = int(width * 0.03)
        y_top = margin

        # Font sizes
        title_size = int(min(height * 0.05, width * 0.05) * scale)
        symbol_size = int(min(height * 0.07, width * 0.07) * scale)
        price_size = int(min(height * 0.065, width * 0.065) * scale)
        change_size = int(min(height * 0.05, width * 0.05) * scale)
        detail_size = int(min(height * 0.035, width * 0.035) * scale)
        footer_size = int(min(height * 0.035, width * 0.035) * footer_scale)

        title_font = get_font("Jost", title_size, "bold")
        symbol_font = get_font("Jost", symbol_size, "bold")
        price_font = get_font("Jost", price_size, "bold")
        change_font = get_font("Jost", change_size, "bold")
        detail_font = get_font("Jost", detail_size)
        footer_font = get_font("Jost", footer_size, "bold")

        # Title
        if title:
            tw = get_text_dimensions(draw, title, title_font)[0]
            draw.text(((width - tw) // 2, y_top), title, font=title_font, fill=text_color)
            th = int(title_size * 1.15)
            y_top += th + int(height * 0.02)
            draw.line((margin, y_top, width - margin, y_top), fill=text_color, width=2)
            y_top += int(height * 0.01)

        # Footer
        footer_h = get_text_dimensions(draw, "X", footer_font)[1] + int(height * 0.02)
        y_bottom = height - margin

        # Grid area
        grid_gap = int(width * 0.015)
        grid_area = (margin, y_top, width - margin * 2, y_bottom - footer_h - y_top)
        cells = calculate_grid(grid_area, rows, columns, grid_gap)

        # Pre-measure line heights for even distribution
        sym_h = int(symbol_size * 1.15)
        price_h = int(price_size * 1.15)
        name_line_h = get_text_dimensions(draw, "X", detail_font)[1]
        change_line_h = get_text_dimensions(draw, "X", change_font)[1]
        detail_line_h = get_text_dimensions(draw, "X", detail_font)[1]

        for i, stock in enumerate(stocks):
            if i >= len(cells):
                break
            cx, cy, cw, ch = cells[i]
            # Card border
            draw.rectangle((cx, cy, cx + cw, cy + ch), outline=text_color, width=2)

            pad = int(cw * 0.05)
            ix = cx + pad
            iw = cw - pad * 2

            # Check if symbol + price fit on one line
            sym_text = stock["symbol"]
            sym_w = get_text_dimensions(draw, sym_text, symbol_font)[0]
            price_w = get_text_dimensions(draw, stock["price_formatted"], price_font)[0]
            arrow_text = " +" if stock["is_positive"] else " -"
            stacked = (sym_w + price_w + int(iw * 0.1)) > iw

            # Check if H/52W detail lines need to stack vertically
            h_text = f"H: {stock['high_formatted']}"
            h52_text = f"52W H: {stock['week52_high_formatted']}"
            h_w = get_text_dimensions(draw, h_text, detail_font)[0]
            h52_w = get_text_dimensions(draw, h52_text, detail_font)[0]
            narrow_card = (h_w + h52_w + int(iw * 0.05)) > iw

            # Calculate total content height
            if stacked:
                sym_line_h = sym_h + price_h
            else:
                sym_line_h = max(sym_h, price_h)
            detail_lines = 5 if narrow_card else 3  # Vol, H, 52W H, L, 52W L vs side-by-side
            total_content_h = sym_line_h + name_line_h + change_line_h + detail_line_h * detail_lines

            # Distribute remaining space evenly
            inner_h = ch - pad * 2
            extra = inner_h - total_content_h
            num_gaps = 5 + (2 if narrow_card else 0)
            gap = max(2, extra // num_gaps)
            iy = cy + pad

            # Symbol + Price
            if stacked:
                # Symbol on its own line
                draw.text((ix, iy), sym_text, font=symbol_font, fill=text_color)
                iy += sym_h
                # Price on next line
                draw.text((ix, iy), stock["price_formatted"], font=price_font, fill=text_color)
                iy += price_h + gap
            else:
                # Symbol left, price right on same line
                draw.text((ix, iy), sym_text, font=symbol_font, fill=text_color)
                pd_w = get_text_dimensions(draw, stock["price_formatted"], price_font)[0]
                draw.text((ix + iw - pd_w, iy), stock["price_formatted"], font=price_font, fill=text_color)
                iy += sym_line_h + gap

            # Company name
            name = truncate_text(draw, stock["name"], detail_font, iw - 2)
            draw.text((ix, iy), name, font=detail_font, fill=text_color)
            iy += name_line_h + gap

            # Change
            change_text = f"{stock['change_formatted']} ({stock['change_percent_formatted']})"
            chg_color = positive_color if stock["is_positive"] else negative_color
            draw.text((ix, iy), change_text, font=change_font, fill=chg_color)
            iy += change_line_h + gap

            # Details: Vol, H/52W H, L/52W L
            mid_x = ix + iw // 2
            draw.text((ix, iy), f"Vol: {stock['volume']}", font=detail_font, fill=text_color)
            iy += detail_line_h + gap * 2 // 3

            if narrow_card:
                # Stack on separate lines
                draw.text((ix, iy), h_text, font=detail_font, fill=text_color)
                iy += detail_line_h + gap * 2 // 3
                draw.text((ix, iy), h52_text, font=detail_font, fill=text_color)
                iy += detail_line_h + gap * 2 // 3
                draw.text((ix, iy), f"L: {stock['low_formatted']}", font=detail_font, fill=text_color)
                iy += detail_line_h + gap * 2 // 3
                draw.text((ix, iy), f"52W L: {stock['week52_low_formatted']}", font=detail_font, fill=text_color)
                iy += detail_line_h + gap * 2 // 3
            else:
                # Side-by-side (horizontal mode)
                draw.text((ix, iy), h_text, font=detail_font, fill=text_color)
                draw.text((mid_x, iy), h52_text, font=detail_font, fill=text_color)
                iy += detail_line_h + gap * 2 // 3
                draw.text((ix, iy), f"L: {stock['low_formatted']}", font=detail_font, fill=text_color)
                draw.text((mid_x, iy), f"52W L: {stock['week52_low_formatted']}", font=detail_font, fill=text_color)
                iy += detail_line_h + gap * 2 // 3

        # Footer
        fy = y_bottom - footer_h
        draw.line((margin, fy, width - margin, fy), fill=text_color, width=1)
        fy += int(height * 0.005)
        refresh_text = f"Refreshes every {auto_refresh_mins} min" if auto_refresh_mins > 0 else "Manual refresh"
        draw.text((margin, fy), refresh_text, font=footer_font, fill=text_color)
        # Market status (centered)
        market_text = "Market Open" if market_open else "Market Closed"
        market_color = positive_color if market_open else negative_color
        mw = get_text_dimensions(draw, market_text, footer_font)[0]
        draw.text(((width - mw) // 2, fy), market_text, font=footer_font, fill=market_color)
        updated_text = f"Last Updated: {last_updated}"
        uw = get_text_dimensions(draw, updated_text, footer_font)[0]
        draw.text((width - margin - uw, fy), updated_text, font=footer_font, fill=text_color)

        return image

    def fetch_stock_data(self, tickers):
        """Fetch stock data for a list of ticker symbols using batch request.

        Results are cached with a TTL that varies by market status (shorter when
        open, longer when closed). yfinance calls are wrapped in a thread timeout
        to prevent hanging the refresh loop.
        """
        import yfinance as yf
        from concurrent.futures import ThreadPoolExecutor, TimeoutError

        # Check cache
        now = time.monotonic()
        cache_ttl = CACHE_TTL_MARKET_OPEN if is_market_open() else CACHE_TTL_MARKET_CLOSED
        if (self._stocks_cache is not None
                and self._stocks_cache_tickers == tickers
                and now - self._stocks_cache_time < cache_ttl):
            logger.info(f"Using cached stock data ({now - self._stocks_cache_time:.0f}s old)")
            return self._stocks_cache

        stocks_data = []

        try:
            # Batch fetch all tickers at once
            tickers_obj = yf.Tickers(" ".join(tickers))

            for symbol in tickers:
                try:
                    # Wrap .info access in a timeout to prevent hanging
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(lambda s=symbol: tickers_obj.tickers[s].info)
                        info = future.result(timeout=YFINANCE_TIMEOUT)

                    # Get current price and other data
                    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
                    previous_close = info.get("previousClose") or info.get("regularMarketPreviousClose")

                    if current_price is None:
                        logger.warning(f"Could not fetch price for {symbol}")
                        continue

                    # Calculate change
                    change = 0
                    change_percent = 0
                    if previous_close and current_price:
                        change = current_price - previous_close
                        change_percent = (change / previous_close) * 100

                    sign = "+" if change >= 0 else ""
                    stocks_data.append({
                        "symbol": symbol,
                        "name": info.get("shortName") or info.get("longName") or symbol,
                        "price_formatted": format_price(current_price),
                        "change_formatted": f"{sign}{change:.2f}",
                        "change_percent_formatted": f"{sign}{change_percent:.2f}%",
                        "volume": format_large_number(info.get("volume") or info.get("regularMarketVolume")),
                        "high_formatted": format_price(info.get("dayHigh") or info.get("regularMarketDayHigh")),
                        "low_formatted": format_price(info.get("dayLow") or info.get("regularMarketDayLow")),
                        "week52_high_formatted": format_price(info.get("fiftyTwoWeekHigh")),
                        "week52_low_formatted": format_price(info.get("fiftyTwoWeekLow")),
                        "is_positive": change >= 0
                    })

                except TimeoutError:
                    logger.warning(f"Timeout fetching data for {symbol} after {YFINANCE_TIMEOUT}s")
                    continue
                except Exception as e:
                    logger.error(f"Error processing data for {symbol}: {str(e)}")
                    continue

        except Exception as e:
            logger.error(f"Error fetching stock data: {str(e)}")

        # Update cache
        if stocks_data:
            self._stocks_cache = stocks_data
            self._stocks_cache_time = now
            self._stocks_cache_tickers = tickers

        return stocks_data
