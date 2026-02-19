#!/usr/bin/env python3
"""Display DashPi splash with animated progress bar on the framebuffer.

Renders "dashpi" text with a thin progress bar that fills over BOOT_DURATION
seconds. Writes directly to /dev/fb0 each frame. Exits when the main DashPi
service takes over the display or after timeout.
"""

import os
import struct
import sys
import time

from PIL import Image, ImageDraw, ImageFont

FB_DEVICE = "/dev/fb0"
SYSFS_VIRTUAL_SIZE = "/sys/class/graphics/fb0/virtual_size"
SYSFS_BPP = "/sys/class/graphics/fb0/bits_per_pixel"
SYSFS_STRIDE = "/sys/class/graphics/fb0/stride"

EXPECTED_BOOT = 40  # expected seconds from animation start to app ready
MAX_WAIT = 120  # absolute timeout
UPDATE_INTERVAL = 0.5  # seconds between frame updates


def read_fb_geometry():
    width, height = 1024, 600
    bpp, stride = 32, None
    try:
        with open(SYSFS_VIRTUAL_SIZE) as f:
            parts = f.read().strip().split(",")
            width, height = int(parts[0]), int(parts[1])
    except Exception:
        pass
    try:
        with open(SYSFS_BPP) as f:
            bpp = int(f.read().strip())
    except Exception:
        pass
    try:
        with open(SYSFS_STRIDE) as f:
            stride = int(f.read().strip())
    except Exception:
        pass
    if stride is None:
        stride = width * (bpp // 8)
    return width, height, bpp, stride


def get_font(font_dir, size):
    for name in ("Jost.ttf", "Jost-SemiBold.ttf", "Jost-Regular.ttf", "Jost-Medium.ttf"):
        # Try both flat and subdirectory layouts
        for subdir in ("", "Jost"):
            path = os.path.join(font_dir, subdir, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, int(size))
    return ImageFont.load_default()


def convert_to_fb(image, bpp, stride):
    if bpp == 16:
        return convert_rgb565(image, stride)
    else:
        return convert_bgra(image, stride)


def convert_bgra(image, stride):
    try:
        import numpy as np
        rgba = image.convert("RGBA")
        arr = np.array(rgba)
        bgra = arr[:, :, [2, 1, 0, 3]]
        pixel_width = bgra.shape[1] * 4
        if stride == pixel_width:
            return bgra.tobytes()
        pad = stride - pixel_width
        return b"".join(row.tobytes() + b"\x00" * pad for row in bgra)
    except ImportError:
        rgba = image.convert("RGBA")
        pixels = list(rgba.getdata())
        w = image.width
        pad = stride - w * 4
        rows = []
        for y in range(image.height):
            row = b""
            for x in range(w):
                r, g, b, a = pixels[y * w + x]
                row += struct.pack("BBBB", b, g, r, a)
            if pad > 0:
                row += b"\x00" * pad
            rows.append(row)
        return b"".join(rows)


def convert_rgb565(image, stride):
    try:
        import numpy as np
        rgb = np.array(image.convert("RGB"), dtype=np.uint16)
        r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        pixel_width = rgb565.shape[1] * 2
        if stride == pixel_width:
            return rgb565.astype("<u2").tobytes()
        pad = stride - pixel_width
        return b"".join(
            row.astype("<u2").tobytes() + b"\x00" * pad for row in rgb565
        )
    except ImportError:
        rgb = image.convert("RGB")
        pixels = list(rgb.getdata())
        w = image.width
        pad = stride - w * 2
        rows = []
        for y in range(image.height):
            row = b""
            for x in range(w):
                r, g, b = pixels[y * w + x]
                v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                row += struct.pack("<H", v)
            if pad > 0:
                row += b"\x00" * pad
            rows.append(row)
        return b"".join(rows)


def render_frame(width, height, font, title_font_size, progress):
    """Render a splash frame with progress bar at given fill (0.0 to 1.0)."""
    bg_color = (255, 255, 255)
    text_color = (0, 0, 0)
    bar_bg = (220, 220, 220)
    bar_fg = (80, 80, 80)

    image = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(image)
    draw.text(
        (width / 2, height * 0.45),
        "DashPi",
        anchor="mm",
        fill=text_color,
        font=font,
    )

    # Progress bar: thin, centered between title and bottom
    bar_width = int(width * 0.45)
    bar_height = max(4, int(height * 0.008))
    bar_x = (width - bar_width) // 2
    title_bottom = int(height * 0.45 + title_font_size * 0.5)
    bar_y = (title_bottom + height) // 2

    # Background track
    draw.rounded_rectangle(
        [bar_x, bar_y, bar_x + bar_width, bar_y + bar_height],
        radius=bar_height // 2,
        fill=bar_bg,
    )

    # Filled portion
    fill_width = max(bar_height, int(bar_width * progress))
    draw.rounded_rectangle(
        [bar_x, bar_y, bar_x + fill_width, bar_y + bar_height],
        radius=bar_height // 2,
        fill=bar_fg,
    )

    return image


def main():
    font_dir = sys.argv[1] if len(sys.argv) > 1 else "/usr/local/dashpi/src/static/fonts"

    width, height, bpp, stride = read_fb_geometry()
    title_font_size = width * 0.25
    font = get_font(font_dir, title_font_size)

    import math
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start

        # Linear ramp to 0.95 over EXPECTED_BOOT, then creeps slowly
        if elapsed <= EXPECTED_BOOT:
            progress = 0.95 * (elapsed / EXPECTED_BOOT)
        else:
            overshoot = elapsed - EXPECTED_BOOT
            progress = 0.95 + 0.04 * (1.0 - math.exp(-0.1 * overshoot))

        image = render_frame(width, height, font, title_font_size, progress)
        fb_bytes = convert_to_fb(image, bpp, stride)

        try:
            with open(FB_DEVICE, "wb") as fb:
                fb.write(fb_bytes)
        except Exception:
            break

        if elapsed >= MAX_WAIT:
            break

        time.sleep(UPDATE_INTERVAL)


if __name__ == "__main__":
    main()
