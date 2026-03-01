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


def generate_wifi_setup_image(dimensions, ap_ssid, portal_url="http://10.42.0.1/wifi",
                               password=None):
    """Generate a display image for WiFi setup mode.

    Shows the AP hotspot name, password, connection instructions, and a QR code
    linking to the captive portal. Designed to be readable on both high-res LCD
    and low-res e-ink displays.

    Args:
        dimensions: Tuple of (width, height) in pixels.
        ap_ssid: The WiFi hotspot SSID to display (e.g., "Lumi-Setup").
        portal_url: URL for the captive portal (e.g., "http://10.42.0.1/wifi").
        password: Hotspot password. Required for connection.

    Returns:
        PIL Image in RGB mode, ready for display_manager.display_image().
    """
    width, height = dimensions
    bg_color = (255, 255, 255)
    text_color = (0, 0, 0)
    accent_color = (26, 188, 156)  # DashPi teal #1abc9c
    muted_color = (100, 100, 100)

    image = Image.new("RGB", dimensions, bg_color)
    draw = ImageDraw.Draw(image)

    # Scale font sizes relative to display width
    title_size = int(width * 0.055)
    label_size = int(width * 0.028)
    value_size = int(width * 0.045)
    instruction_size = int(width * 0.028)
    small_size = int(width * 0.022)

    # Layout: compact top section, QR in center, instructions below
    y_title = height * 0.07
    y_network_label = height * 0.15
    y_network_value = height * 0.20
    y_password_label = height * 0.27
    y_password_value = height * 0.32
    y_qr_center = height * 0.52
    y_instructions_start = height * 0.73

    # --- Title ---
    title_font = get_font("Jost", title_size, "bold")
    draw.text(
        (width / 2, y_title), "WiFi Setup Required",
        anchor="mm", fill=accent_color, font=title_font
    )

    # --- Network name ---
    label_font = get_font("Jost", label_size)
    value_font = get_font("Jost", value_size, "bold")

    draw.text(
        (width / 2, y_network_label), "On your phone, join this WiFi network:",
        anchor="mm", fill=muted_color, font=label_font
    )
    draw.text(
        (width / 2, y_network_value), ap_ssid,
        anchor="mm", fill=text_color, font=value_font
    )

    # --- Password ---
    if password:
        draw.text(
            (width / 2, y_password_label), "Password:",
            anchor="mm", fill=muted_color, font=label_font
        )
        draw.text(
            (width / 2, y_password_value), password,
            anchor="mm", fill=text_color, font=value_font
        )

    # --- QR Code ---
    qr_size = int(min(width, height) * 0.25)

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
            url_font = get_font("Jost", instruction_size)
            draw.text(
                (width / 2, y_qr_center), portal_url,
                anchor="mm", fill=accent_color, font=url_font
            )
    else:
        url_font = get_font("Jost", instruction_size)
        draw.text(
            (width / 2, y_qr_center), portal_url,
            anchor="mm", fill=accent_color, font=url_font
        )

    # --- Instructions ---
    instr_font = get_font("Jost", instruction_size)
    small_font = get_font("Jost", small_size)

    instructions = [
        "1.  Connect your phone to the network above",
        "2.  A setup page will open  —  or scan the QR code",
        "3.  Choose your home WiFi and enter its password",
    ]

    y = y_instructions_start
    line_spacing = height * 0.05
    for line in instructions:
        draw.text(
            (width / 2, y), line,
            anchor="mm", fill=text_color, font=instr_font
        )
        y += line_spacing

    # --- Footer ---
    draw.text(
        (width / 2, height * 0.94),
        f"Or visit {portal_url} after connecting",
        anchor="mm", fill=(150, 150, 150), font=small_font
    )

    return image
