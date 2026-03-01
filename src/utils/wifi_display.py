"""WiFi setup display — generates the 'No WiFi' screen shown on the physical display.

When DashPi enters AP hotspot mode, this module renders a PIL image with
setup instructions and a QR code for the captive portal URL. Works on
both LCD and e-ink displays.
"""

import logging

from PIL import Image, ImageDraw
from utils.app_utils import get_font

logger = logging.getLogger(__name__)

# Try to import qrcode; gracefully degrade if not installed
try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False
    logger.debug("qrcode library not available, QR codes will be skipped")


def generate_wifi_setup_image(dimensions, ap_ssid, portal_url="http://10.42.0.1",
                               password=None):
    """Generate a display image for WiFi setup mode.

    Shows the AP hotspot name, connection instructions, and optionally a QR code
    linking to the captive portal. Designed to be readable on both high-res LCD
    and low-res e-ink displays.

    Args:
        dimensions: Tuple of (width, height) in pixels.
        ap_ssid: The WiFi hotspot SSID to display (e.g., "Lumi-Setup").
        portal_url: URL for the captive portal (e.g., "http://10.42.0.1").
        password: Optional hotspot password. If None, shown as open network.

    Returns:
        PIL Image in RGB mode, ready for display_manager.display_image().
    """
    width, height = dimensions
    bg_color = (255, 255, 255)
    text_color = (0, 0, 0)
    accent_color = (26, 188, 156)  # DashPi teal #1abc9c

    image = Image.new("RGB", dimensions, bg_color)
    draw = ImageDraw.Draw(image)

    # Scale font sizes relative to display width
    title_size = int(width * 0.06)
    ssid_size = int(width * 0.055)
    instruction_size = int(width * 0.03)
    small_size = int(width * 0.025)

    # Layout: title at top, QR in center, instructions below
    # Vertical spacing proportional to height
    y_title = height * 0.08
    y_ssid = height * 0.18
    y_qr_center = height * 0.48
    y_instructions_start = height * 0.72

    # --- Title ---
    title_font = get_font("Jost", title_size, "bold")
    draw.text(
        (width / 2, y_title), "WiFi Setup",
        anchor="mm", fill=accent_color, font=title_font
    )

    # --- SSID ---
    ssid_font = get_font("Jost", ssid_size)
    draw.text(
        (width / 2, y_ssid), f'Connect to:  "{ap_ssid}"',
        anchor="mm", fill=text_color, font=ssid_font
    )

    # --- Password (if required) ---
    if password:
        pw_y = y_ssid + height * 0.06
        pw_font = get_font("Jost", instruction_size)
        draw.text(
            (width / 2, pw_y), f"Password:  {password}",
            anchor="mm", fill=text_color, font=pw_font
        )

    # --- QR Code ---
    qr_size = int(min(width, height) * 0.3)

    if HAS_QRCODE:
        try:
            qr = qrcode.QRCode(
                version=None,  # Auto-size
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=2,
            )
            qr.add_data(portal_url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")
            qr_img = qr_img.resize((qr_size, qr_size), Image.NEAREST)

            # Center the QR code
            qr_x = (width - qr_size) // 2
            qr_y = int(y_qr_center - qr_size / 2)
            image.paste(qr_img.convert("RGB"), (qr_x, qr_y))
        except Exception as e:
            logger.warning("QR code generation failed: %s", e)
            # Fall back to text-only
            url_font = get_font("Jost", instruction_size)
            draw.text(
                (width / 2, y_qr_center), portal_url,
                anchor="mm", fill=accent_color, font=url_font
            )
    else:
        # No qrcode library — show URL as text
        url_font = get_font("Jost", instruction_size)
        draw.text(
            (width / 2, y_qr_center), portal_url,
            anchor="mm", fill=accent_color, font=url_font
        )

    # --- Instructions ---
    instr_font = get_font("Jost", instruction_size)
    small_font = get_font("Jost", small_size)

    instructions = [
        "1.  Connect your phone to the WiFi above",
        "2.  A setup page will open automatically",
        "3.  Select your WiFi network and enter password",
    ]

    y = y_instructions_start
    line_spacing = height * 0.055
    for line in instructions:
        draw.text(
            (width / 2, y), line,
            anchor="mm", fill=text_color, font=instr_font
        )
        y += line_spacing

    # --- Footer ---
    draw.text(
        (width / 2, height * 0.94),
        f"Or visit {portal_url} in your browser",
        anchor="mm", fill=(128, 128, 128), font=small_font
    )

    return image
