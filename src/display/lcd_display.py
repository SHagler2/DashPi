import logging
import struct
import numpy as np
from display.abstract_display import AbstractDisplay

logger = logging.getLogger(__name__)


class LcdDisplay(AbstractDisplay):
    """
    Drives an HDMI-connected LCD via the Linux framebuffer (/dev/fb0).

    Reads resolution and pixel format from sysfs, converts PIL images
    to raw framebuffer bytes, and writes them directly to /dev/fb0.
    """

    FB_DEVICE = "/dev/fb0"
    SYSFS_VIRTUAL_SIZE = "/sys/class/graphics/fb0/virtual_size"
    SYSFS_BPP = "/sys/class/graphics/fb0/bits_per_pixel"
    SYSFS_STRIDE = "/sys/class/graphics/fb0/stride"

    def initialize_display(self):
        """Read framebuffer geometry from sysfs and store resolution in config."""

        # Resolution
        with open(self.SYSFS_VIRTUAL_SIZE) as f:
            parts = f.read().strip().split(",")
            self.width = int(parts[0])
            self.height = int(parts[1])

        # Bits per pixel
        with open(self.SYSFS_BPP) as f:
            self.bpp = int(f.read().strip())

        # Stride (bytes per scanline, may include padding)
        with open(self.SYSFS_STRIDE) as f:
            self.stride = int(f.read().strip())

        logger.info(
            "LCD framebuffer initialized: %dx%d, %dbpp, stride=%d",
            self.width, self.height, self.bpp, self.stride,
        )

        # Store resolution in device config (same pattern as InkyDisplay)
        if not self.device_config.get_config("resolution"):
            self.device_config.update_value(
                "resolution",
                [self.width, self.height],
                write=True,
            )

    def display_image(self, image, image_settings=None):
        """Write a PIL image to the framebuffer."""

        if not image:
            raise ValueError("No image provided.")

        logger.info("Displaying image to LCD framebuffer.")

        if self.bpp == 32:
            fb_bytes = self._convert_bgra(image)
        elif self.bpp == 16:
            fb_bytes = self._convert_rgb565(image)
        else:
            raise ValueError(f"Unsupported framebuffer depth: {self.bpp}bpp")

        with open(self.FB_DEVICE, "wb") as fb:
            fb.write(fb_bytes)

    # ---- format converters ------------------------------------------------

    def _convert_bgra(self, image):
        """Convert PIL image to BGRA bytes respecting stride."""
        rgba = image.convert("RGBA")
        arr = np.array(rgba)  # shape (H, W, 4) — R, G, B, A
        # Swap R and B channels → BGRA
        bgra = arr[:, :, [2, 1, 0, 3]]

        bytes_per_pixel = 4
        pixel_width = bgra.shape[1] * bytes_per_pixel

        if self.stride == pixel_width:
            return bgra.tobytes()

        # Pad each row to match stride
        pad = self.stride - pixel_width
        return b"".join(
            row.tobytes() + b"\x00" * pad for row in bgra
        )

    def _convert_rgb565(self, image):
        """Convert PIL image to RGB565 (16-bit) bytes respecting stride."""
        rgb = np.array(image.convert("RGB"), dtype=np.uint16)
        r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)

        bytes_per_pixel = 2
        pixel_width = rgb565.shape[1] * bytes_per_pixel

        if self.stride == pixel_width:
            return rgb565.astype("<u2").tobytes()

        # Pad each row to match stride
        pad = self.stride - pixel_width
        return b"".join(
            row.astype("<u2").tobytes() + b"\x00" * pad for row in rgb565
        )
