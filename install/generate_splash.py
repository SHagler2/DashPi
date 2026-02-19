#!/usr/bin/env python3
"""Generate a raw framebuffer splash image for DashPi clean boot.

Reads framebuffer geometry from sysfs (falls back to 1024x600x32bpp),
renders a simple "dashpi" splash, converts to raw BGRA bytes, and writes
to the output path.

Usage:
    python3 generate_splash.py --output /usr/local/dashpi/splash.fb --font-dir /path/to/fonts
"""

import argparse
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

# sysfs paths for framebuffer geometry
SYSFS_VIRTUAL_SIZE = "/sys/class/graphics/fb0/virtual_size"
SYSFS_BPP = "/sys/class/graphics/fb0/bits_per_pixel"
SYSFS_STRIDE = "/sys/class/graphics/fb0/stride"

# Defaults if sysfs is unavailable
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 600
DEFAULT_BPP = 32


def read_fb_geometry():
    """Read framebuffer geometry from sysfs, fall back to defaults."""
    width, height = DEFAULT_WIDTH, DEFAULT_HEIGHT
    bpp = DEFAULT_BPP
    stride = None

    try:
        with open(SYSFS_VIRTUAL_SIZE) as f:
            parts = f.read().strip().split(",")
            width = int(parts[0])
            height = int(parts[1])
    except (FileNotFoundError, ValueError, IndexError):
        pass

    try:
        with open(SYSFS_BPP) as f:
            bpp = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        pass

    try:
        with open(SYSFS_STRIDE) as f:
            stride = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        pass

    if stride is None:
        stride = width * (bpp // 8)

    return width, height, bpp, stride


def get_font(font_dir, size):
    """Load Jost font from the font directory."""
    for name in ("Jost.ttf", "Jost-SemiBold.ttf", "Jost-Regular.ttf", "Jost-Medium.ttf"):
        for subdir in ("", "Jost"):
            path = os.path.join(font_dir, subdir, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, int(size))
    return ImageFont.load_default()


def render_splash(width, height, font_dir):
    """Render the splash image as a PIL Image."""
    bg_color = (255, 255, 255)
    text_color = (0, 0, 0)

    image = Image.new("RGBA", (width, height), bg_color)
    draw = ImageDraw.Draw(image)

    title_font_size = width * 0.25
    font = get_font(font_dir, title_font_size)
    draw.text(
        (width / 2, height * 0.45),
        "DashPi",
        anchor="mm",
        fill=text_color,
        font=font,
    )

    return image


def convert_bgra(image, width, height, stride):
    """Convert PIL image to raw BGRA bytes respecting stride."""
    try:
        import numpy as np
        rgba = image.convert("RGBA")
        arr = np.array(rgba)
        bgra = arr[:, :, [2, 1, 0, 3]]

        bytes_per_pixel = 4
        pixel_width = bgra.shape[1] * bytes_per_pixel

        if stride == pixel_width:
            return bgra.tobytes()

        pad = stride - pixel_width
        return b"".join(row.tobytes() + b"\x00" * pad for row in bgra)
    except ImportError:
        # Fallback without numpy
        rgba = image.convert("RGBA")
        pixels = list(rgba.getdata())
        bytes_per_pixel = 4
        pixel_width = width * bytes_per_pixel
        pad = stride - pixel_width

        rows = []
        for y in range(height):
            row_bytes = b""
            for x in range(width):
                r, g, b, a = pixels[y * width + x]
                row_bytes += struct.pack("BBBB", b, g, r, a)
            if pad > 0:
                row_bytes += b"\x00" * pad
            rows.append(row_bytes)
        return b"".join(rows)


def convert_rgb565(image, width, height, stride):
    """Convert PIL image to RGB565 bytes respecting stride."""
    rgb = image.convert("RGB")
    pixels = list(rgb.getdata())
    bytes_per_pixel = 2
    pixel_width = width * bytes_per_pixel
    pad = stride - pixel_width

    rows = []
    for y in range(height):
        row_bytes = b""
        for x in range(width):
            r, g, b = pixels[y * width + x]
            rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            row_bytes += struct.pack("<H", rgb565)
        if pad > 0:
            row_bytes += b"\x00" * pad
        rows.append(row_bytes)
    return b"".join(rows)


def main():
    parser = argparse.ArgumentParser(description="Generate DashPi splash framebuffer image")
    parser.add_argument("--output", required=True, help="Output path for raw framebuffer file")
    parser.add_argument("--font-dir", required=True, help="Path to fonts directory")
    args = parser.parse_args()

    width, height, bpp, stride = read_fb_geometry()
    print(f"Framebuffer: {width}x{height}, {bpp}bpp, stride={stride}")

    image = render_splash(width, height, args.font_dir)

    if bpp == 32:
        fb_bytes = convert_bgra(image, width, height, stride)
    elif bpp == 16:
        fb_bytes = convert_rgb565(image, width, height, stride)
    else:
        print(f"Unsupported framebuffer depth: {bpp}bpp", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "wb") as f:
        f.write(fb_bytes)

    size_kb = len(fb_bytes) / 1024
    print(f"Splash image written to {args.output} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
