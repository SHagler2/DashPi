from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw
from utils.app_utils import get_font
from utils.text_utils import get_text_dimensions, truncate_text
from utils.layout_utils import calculate_grid
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

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


class Stocks(BasePlugin):
    """Stock ticker dashboard plugin using yfinance.

    Displays up to 6 stock tickers in a responsive grid layout. Each card shows
    the symbol, company name, current price, daily change (colored green/red),
    volume, and day high/low. Supports configurable font sizes and auto-refresh.
    """

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
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
        last_updated = datetime.now().strftime("%I:%M %p")

        return self._render_pil(dimensions, title, stocks_data, columns, rows,
                                last_updated, auto_refresh_mins,
                                font_scale * count_scale, settings)

    def _render_pil(self, dimensions, title, stocks, columns, rows,
                    last_updated, auto_refresh_mins, scale, settings):
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
            scale: Combined font_size * count_scale multiplier.
            settings: Plugin settings dict (colors, etc.).

        Returns:
            PIL Image (RGBA) ready for display.
        """
        width, height = dimensions
        bg_color = settings.get("backgroundColor", "#ffffff")
        text_color = settings.get("textColor", "#000000")

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
        footer_size = int(min(height * 0.035, width * 0.035) * scale)

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

        positive_color = "#006400"
        negative_color = "#8B0000"

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

            # Calculate total content height
            if stacked:
                sym_line_h = sym_h + price_h
            else:
                sym_line_h = max(sym_h, price_h)
            total_content_h = sym_line_h + name_line_h + change_line_h + detail_line_h * 3

            # Distribute remaining space evenly
            inner_h = ch - pad * 2
            extra = inner_h - total_content_h
            num_gaps = 5
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

            # Details: Vol, H, L
            for detail in [f"Vol: {stock['volume']}", f"H: {stock['high_formatted']}", f"L: {stock['low_formatted']}"]:
                draw.text((ix, iy), detail, font=detail_font, fill=text_color)
                iy += detail_line_h + gap * 2 // 3

        # Footer
        fy = y_bottom - footer_h
        draw.line((margin, fy, width - margin, fy), fill=text_color, width=1)
        fy += int(height * 0.005)
        refresh_text = f"Refreshes every {auto_refresh_mins} min" if auto_refresh_mins > 0 else "Manual refresh"
        draw.text((margin, fy), refresh_text, font=footer_font, fill=text_color)
        updated_text = f"Last Updated: {last_updated}"
        uw = get_text_dimensions(draw, updated_text, footer_font)[0]
        draw.text((width - margin - uw, fy), updated_text, font=footer_font, fill=text_color)

        return image

    def fetch_stock_data(self, tickers):
        """Fetch stock data for a list of ticker symbols using batch request."""
        import yfinance as yf
        stocks_data = []

        try:
            # Batch fetch all tickers at once
            tickers_obj = yf.Tickers(" ".join(tickers))

            for symbol in tickers:
                try:
                    info = tickers_obj.tickers[symbol].info

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
                        "is_positive": change >= 0
                    })

                except Exception as e:
                    logger.error(f"Error processing data for {symbol}: {str(e)}")
                    continue

        except Exception as e:
            logger.error(f"Error fetching stock data: {str(e)}")

        return stocks_data
