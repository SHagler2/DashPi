from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageOps, ImageColor, ImageDraw, ImageFont
import logging
import random
import os

from utils.app_utils import get_font
from utils.image_utils import pad_image_blur

logger = logging.getLogger(__name__)


class ImageUpload(BasePlugin):
    def open_image(self, img_index: int, image_locations: list, dimensions: tuple, resize: bool = True, fit_mode: str = 'fill') -> Image:
        """
        Open image with adaptive loader for memory efficiency.

        Args:
            img_index: Index of image to load
            image_locations: List of image paths
            dimensions: Target dimensions
            resize: Whether to auto-resize
            fit_mode: How to fit image ('fit', 'fill', or 'blur')
        """
        if not image_locations:
            raise RuntimeError("No images provided.")

        try:
            image = self.image_loader.from_file(image_locations[img_index], dimensions, resize=resize, fit_mode=fit_mode)
            if not image:
                raise RuntimeError("Failed to load image from file")
            return image
        except Exception as e:
            logger.error(f"Failed to read image file: {str(e)}")
            raise RuntimeError("Failed to read image file.")


    def generate_image(self, settings, device_config) -> Image:
        logger.info("=== Image Upload Plugin: Starting image generation ===")

        # Ensure _previous_files is available (form POST doesn't include it)
        if '_previous_files' not in settings:
            stored = device_config.get_config("plugin_last_settings_image_upload", default={})
            settings['_previous_files'] = stored.get('_previous_files', [])

        # Reconcile: add any files on disk that aren't in settings (recovers from crashes)
        self._reconcile_with_disk(settings)

        # Get the current index — check stored settings if not in form POST
        img_index = settings.get("image_index")
        if img_index is None:
            stored = device_config.get_config("plugin_last_settings_image_upload", default={})
            img_index = stored.get("image_index", 0)
        image_locations = settings.get("imageFiles[]")

        if not image_locations:
            logger.error("No images uploaded")
            raise RuntimeError("No images provided.")

        logger.debug(f"Total uploaded images: {len(image_locations)}")
        logger.debug(f"Current index: {img_index}")

        if img_index >= len(image_locations):
            # Prevent Index out of range issues when file list has changed
            logger.warning(f"Index {img_index} out of range, resetting to 0")
            img_index = 0

        # Get dimensions
        dimensions = device_config.get_resolution()
        orientation = device_config.get_config("orientation")
        if orientation == "vertical":
            dimensions = dimensions[::-1]
            logger.debug(f"Vertical orientation detected, dimensions: {dimensions[0]}x{dimensions[1]}")

        # Display mode: fit (letterbox), fill (crop), or blur (blurred background)
        # Migrate old padImage setting if fitMode not set
        fit_mode = settings.get('fitMode')
        if not fit_mode:
            if settings.get('padImage') == 'true':
                fit_mode = 'blur' if settings.get('backgroundOption', 'blur') == 'blur' else 'fit'
            else:
                fit_mode = 'fit'

        is_random = settings.get('randomize') == 'true'

        logger.debug(f"Settings: randomize={is_random}, fit_mode={fit_mode}")

        # For blur mode, load without resize (manual padding needed)
        # For fit/fill, let the image loader handle it natively
        use_native_resize = fit_mode in ('fit', 'fill')

        if is_random:
            img_index = random.randrange(0, len(image_locations))
            logger.info(f"Random mode: Selected image index {img_index}")
        else:
            logger.info(f"Sequential mode: Loading image index {img_index}")

        image = self.open_image(img_index, image_locations, dimensions, resize=use_native_resize, fit_mode=fit_mode)

        if not is_random:
            img_index = (img_index + 1) % len(image_locations)
            logger.debug(f"Next index will be: {img_index}")

        # Write the new index back to the device json
        settings['image_index'] = img_index

        # Track current file list for future cleanup
        settings['_previous_files'] = list(image_locations)

        # Blur mode: manual padding with blurred background
        if fit_mode == 'blur':
            logger.debug("Applying blur background padding")
            image = pad_image_blur(image, dimensions)
        elif fit_mode == 'fit':
            # Apply letterbox color if specified (default black)
            bg_color = settings.get('backgroundColor', '#000000')
            if bg_color and bg_color != '#000000':
                background_color = ImageColor.getcolor(bg_color, image.mode)
                image = ImageOps.pad(image, dimensions, color=background_color, method=Image.Resampling.LANCZOS)

        # Overlay filename if enabled
        if settings.get('showFilename') == 'true':
            filename = os.path.basename(image_locations[img_index - 1 if not is_random else img_index])
            # Strip extension
            name_without_ext = os.path.splitext(filename)[0]
            image = self._add_filename_overlay(image, name_without_ext)

        logger.info("=== Image Upload Plugin: Image generation complete ===")
        return image

    def _add_filename_overlay(self, image, filename):
        """Add a semi-transparent filename label at the bottom of the image."""
        draw = ImageDraw.Draw(image, 'RGBA')
        w, h = image.size

        font_size = max(14, int(h * 0.03))
        try:
            font = get_font("Jost", font_size)
        except (OSError, IOError):
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), filename, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        padding = 8
        x = (w - text_w) // 2
        y = h - text_h - padding * 3

        # Semi-transparent background
        draw.rectangle(
            [x - padding, y - padding, x + text_w + padding, y + text_h + padding],
            fill=(0, 0, 0, 140)
        )
        draw.text((x, y), filename, fill=(255, 255, 255, 230), font=font)

        return image

    def _reconcile_with_disk(self, settings):
        """Add any files on disk that aren't in the settings list (recovers from crashes).

        Only adds files that weren't recently removed by the user. This prevents
        reconciliation from undoing intentional deletions via the web UI.
        """
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.heif', '.heic', '.avif'}
        saved_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'static', 'images', 'saved')

        if not os.path.isdir(saved_dir):
            return

        current_files = set(settings.get('imageFiles[]', []))
        current_basenames = {os.path.basename(f) for f in current_files}

        # Files the user previously had — if a file was in previous but not current,
        # the user intentionally removed it, so don't re-add it
        previous_files = set(settings.get('_previous_files', []))
        removed_basenames = {os.path.basename(f) for f in previous_files - current_files}

        added = 0
        for filename in sorted(os.listdir(saved_dir)):
            if filename.startswith('.'):
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in image_extensions:
                continue
            if filename in current_basenames:
                continue
            if filename in removed_basenames:
                continue  # User intentionally removed this file
            full_path = os.path.join(saved_dir, filename)
            if 'imageFiles[]' not in settings:
                settings['imageFiles[]'] = []
            settings['imageFiles[]'].append(full_path)
            current_basenames.add(filename)
            added += 1

        if added:
            logger.info(f"Reconciled {added} image(s) from disk that were missing from settings")

    def _cleanup_removed_files(self, settings):
        """Delete files that were removed from the image list since last run."""
        previous_files = set(settings.get('_previous_files', []))
        current_files = set(settings.get('imageFiles[]', []))

        removed = previous_files - current_files
        for file_path in removed:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"Deleted removed image: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete removed image {file_path}: {e}")

    def cleanup(self, settings):
        """Delete all uploaded image files associated with this plugin instance."""
        image_locations = settings.get("imageFiles[]", [])
        if not image_locations:
            return

        for image_path in image_locations:
            if os.path.exists(image_path):
                try:
                    os.remove(image_path)
                    logger.info(f"Deleted uploaded image: {image_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete uploaded image {image_path}: {e}")
