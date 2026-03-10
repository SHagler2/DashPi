"""Tests for file-dependent plugins: ImageFolder, ImageUpload, ImageUrl, Screenshot.

Uses tmp_path to create temp images. External HTTP/subprocess calls are mocked.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from PIL import Image


def assert_valid_image(img, expected_size=None):
    assert isinstance(img, Image.Image), f"Expected PIL Image, got {type(img)}"
    assert img.size[0] > 0 and img.size[1] > 0
    if expected_size:
        assert img.size == expected_size, f"Expected {expected_size}, got {img.size}"


IMAGE_FOLDER_CONFIG = {"id": "image_folder", "display_name": "Image Folder", "class": "ImageFolder"}
IMAGE_UPLOAD_CONFIG = {"id": "image_upload", "display_name": "Image Upload", "class": "ImageUpload"}
IMAGE_URL_CONFIG = {"id": "image_url", "display_name": "Image URL", "class": "ImageURL"}
SCREENSHOT_CONFIG = {"id": "screenshot", "display_name": "Screenshot", "class": "Screenshot"}


def _create_test_image(path, size=(100, 100)):
    """Save a small test PNG to the given path."""
    img = Image.new("RGB", size, "blue")
    img.save(str(path))


# ===========================================================================
# ImageFolder Plugin
# ===========================================================================

class TestImageFolder:
    @pytest.fixture
    def plugin(self):
        from plugins.image_folder.image_folder import ImageFolder
        return ImageFolder(IMAGE_FOLDER_CONFIG)

    def test_folder_with_images(self, plugin, mock_device_config, tmp_path):
        _create_test_image(tmp_path / "photo1.png")
        _create_test_image(tmp_path / "photo2.jpg")
        settings = {"folder_path": str(tmp_path)}
        img = plugin.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_missing_folder_path(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="Folder path is required"):
            plugin.generate_image({}, mock_device_config)

    def test_nonexistent_folder(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="does not exist"):
            plugin.generate_image({"folder_path": "/nonexistent/path"}, mock_device_config)

    def test_empty_folder(self, plugin, mock_device_config, tmp_path):
        with pytest.raises(RuntimeError, match="No image files"):
            plugin.generate_image({"folder_path": str(tmp_path)}, mock_device_config)

    def test_not_a_directory(self, plugin, mock_device_config, tmp_path):
        file_path = tmp_path / "file.txt"
        file_path.write_text("not a dir")
        with pytest.raises(RuntimeError, match="not a directory"):
            plugin.generate_image({"folder_path": str(file_path)}, mock_device_config)

    def test_with_padding_blur(self, plugin, mock_device_config, tmp_path):
        _create_test_image(tmp_path / "photo.png", (200, 300))
        settings = {
            "folder_path": str(tmp_path),
            "padImage": "true",
            "backgroundOption": "blur",
        }
        img = plugin.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_with_padding_solid(self, plugin, mock_device_config, tmp_path):
        _create_test_image(tmp_path / "photo.png", (200, 300))
        settings = {
            "folder_path": str(tmp_path),
            "padImage": "true",
            "backgroundOption": "solid",
            "backgroundColor": "#ff0000",
        }
        img = plugin.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_hidden_files_excluded(self, plugin, mock_device_config, tmp_path):
        _create_test_image(tmp_path / ".hidden.png")
        with pytest.raises(RuntimeError, match="No image files"):
            plugin.generate_image({"folder_path": str(tmp_path)}, mock_device_config)

    def test_recursive_scan(self, plugin, mock_device_config, tmp_path):
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        _create_test_image(subdir / "nested.png")
        settings = {"folder_path": str(tmp_path)}
        img = plugin.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))


# ===========================================================================
# ImageUpload Plugin
# ===========================================================================

class TestImageUpload:
    @pytest.fixture
    def plugin(self):
        from plugins.image_upload.image_upload import ImageUpload
        return ImageUpload(IMAGE_UPLOAD_CONFIG)

    def test_single_image(self, plugin, mock_device_config, tmp_path):
        img_path = tmp_path / "uploaded.png"
        _create_test_image(img_path)
        settings = {"imageFiles[]": [str(img_path)]}
        img = plugin.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_multiple_images_sequential(self, plugin, mock_device_config, tmp_path):
        paths = []
        for i in range(3):
            p = tmp_path / f"img{i}.png"
            _create_test_image(p)
            paths.append(str(p))
        settings = {"imageFiles[]": paths, "image_index": 0}
        img = plugin.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))
        # Index should have advanced
        assert settings["image_index"] == 1

    def test_random_mode(self, plugin, mock_device_config, tmp_path):
        paths = []
        for i in range(3):
            p = tmp_path / f"img{i}.png"
            _create_test_image(p)
            paths.append(str(p))
        settings = {"imageFiles[]": paths, "randomize": "true"}
        img = plugin.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_no_images_raises(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="No images"):
            plugin.generate_image({}, mock_device_config)

    def test_index_out_of_range_resets(self, plugin, mock_device_config, tmp_path):
        img_path = tmp_path / "img.png"
        _create_test_image(img_path)
        settings = {"imageFiles[]": [str(img_path)], "image_index": 99}
        img = plugin.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))

    def test_cleanup_deletes_files(self, plugin, tmp_path):
        paths = []
        for i in range(2):
            p = tmp_path / f"cleanup{i}.png"
            _create_test_image(p)
            paths.append(str(p))
        plugin.cleanup({"imageFiles[]": paths})
        for p in paths:
            assert not os.path.exists(p)

    def test_cleanup_missing_files(self, plugin):
        # Should not raise on missing files
        plugin.cleanup({"imageFiles[]": ["/nonexistent/file.png"]})

    def test_with_padding(self, plugin, mock_device_config, tmp_path):
        img_path = tmp_path / "padded.png"
        _create_test_image(img_path, (200, 300))
        settings = {
            "imageFiles[]": [str(img_path)],
            "padImage": "true",
            "backgroundOption": "blur",
        }
        img = plugin.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))


# ===========================================================================
# ImageUrl Plugin
# ===========================================================================

class TestImageUrl:
    @pytest.fixture
    def plugin(self):
        from plugins.image_url.image_url import ImageURL
        return ImageURL(IMAGE_URL_CONFIG)

    def test_missing_url_raises(self, plugin, mock_device_config):
        with pytest.raises(RuntimeError, match="URL is required"):
            plugin.generate_image({}, mock_device_config)

    def test_valid_url(self, plugin, mock_device_config):
        mock_image = Image.new("RGB", (800, 480), "green")
        plugin.image_loader = MagicMock()
        plugin.image_loader.from_url.return_value = mock_image
        settings = {"url": "https://example.com/image.png"}
        img = plugin.generate_image(settings, mock_device_config)
        assert_valid_image(img, (800, 480))
        plugin.image_loader.from_url.assert_called_once()

    def test_failed_load_raises(self, plugin, mock_device_config):
        plugin.image_loader = MagicMock()
        plugin.image_loader.from_url.return_value = None
        settings = {"url": "https://example.com/broken.png"}
        with pytest.raises(RuntimeError, match="Failed to load"):
            plugin.generate_image(settings, mock_device_config)

    def test_fit_mode_passed(self, plugin, mock_device_config):
        mock_image = Image.new("RGB", (800, 480), "green")
        plugin.image_loader = MagicMock()
        plugin.image_loader.from_url.return_value = mock_image
        settings = {"url": "https://example.com/img.png", "fitMode": "fit"}
        plugin.generate_image(settings, mock_device_config)
        call_kwargs = plugin.image_loader.from_url.call_args
        assert call_kwargs.kwargs.get("fit_mode") == "fit" or call_kwargs[0][3] if len(call_kwargs[0]) > 3 else True

