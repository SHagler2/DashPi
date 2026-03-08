"""
ShazamPi Music Plugin for DashPi
Records audio from a USB microphone, detects music using YAMNet (TFLite),
identifies songs via Shazam, and displays album art with a title overlay.
Falls back to an idle display with weather info when no music is detected.
"""

import asyncio
import csv
import datetime
import io
import json
import logging
import os
import tempfile
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import get_font

logger = logging.getLogger(__name__)

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
ML_MODEL_PATH = os.path.join(PLUGIN_DIR, "ml-model", "1.tflite")
CLASS_MAP_PATH = os.path.join(PLUGIN_DIR, "ml-model", "yamnet_class_map.csv")
DEFAULT_IMAGE_PATH = os.path.join(PLUGIN_DIR, "resources", "default.jpg")

RAW_SAMPLE_RATE = 44100
DOWN_SAMPLE_RATE = 16000
AUDIO_GAIN = 1.0
WEATHER_CACHE_MINUTES = 30
EINK_IDLE_REFRESH_MINUTES = 15  # How often to repaint idle screen on e-ink
SHAZAM_API_TIMEOUT = 15        # Seconds before giving up on Shazam API
ALBUM_ART_TIMEOUT_MS = 8000    # Milliseconds before giving up on album art download
STATUS_FILE = os.path.join(PLUGIN_DIR, "status.json")

_HOUR_WORDS = [
    "twelve", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
]


def _fuzzy_time():
    """Return an approximate time string like 'It's about quarter past three'."""
    now = datetime.datetime.now()
    hour = now.hour % 12
    minute = now.minute

    if minute < 8:
        return f"It's about {_HOUR_WORDS[hour]} o'clock"
    elif minute < 23:
        return f"It's about quarter past {_HOUR_WORDS[hour]}"
    elif minute < 38:
        return f"It's about half past {_HOUR_WORDS[hour]}"
    elif minute < 53:
        return f"It's about quarter to {_HOUR_WORDS[hour + 1]}"
    else:
        return f"It's about {_HOUR_WORDS[hour + 1]} o'clock"


class ShazamPi(BasePlugin):
    """Records audio, identifies songs via Shazam, and displays album art with song info."""

    def __init__(self, config, **deps):
        super().__init__(config, **deps)
        self._interpreter = None
        self._class_names = None
        self._shazam = None
        self._last_song = None
        self._consecutive_misses = 0
        self._last_song_time = 0
        self._last_weather = None
        self._last_weather_fetch = None
        self._recording_duration = None
        self._idle_displayed_at = None  # Timestamp of last idle render (for e-ink dedup)
        self._cached_mic_card = None    # Cached ALSA card number (doesn't change mid-session)
        self._mic_configured = False    # Whether amixer settings have been applied
        self._event_loop = None         # Reusable asyncio event loop for Shazam calls

    def _set_status(self, stage, detail=""):
        """Write current status to a JSON file for the web UI to poll."""
        try:
            status = {
                "stage": stage,
                "detail": detail,
                "timestamp": time.time(),
            }
            with open(STATUS_FILE, 'w') as f:
                json.dump(status, f)
        except Exception:
            pass  # Non-critical, don't break the pipeline

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = False
        template_params['hide_refresh_interval'] = True
        return template_params

    def generate_image(self, settings, device_config):
        """Record audio, identify the song, and render album art with title overlay."""
        logger.info("=== ShazamPi Plugin: Starting ===")
        self._set_status("starting", "Initializing...")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        recording_duration = int(settings.get("recordingDuration", 5))

        # 1. Record audio from USB mic (44.1kHz WAV for Shazam, 16kHz gained for YAMNet)
        self._set_status("recording", f"Recording {recording_duration}s of audio...")
        raw_wav_bytes, gained_audio = self._record_audio(recording_duration)

        # 2. Detect music via YAMNet (uses gain-boosted 16kHz audio for sensitivity)
        self._set_status("detecting", "Analyzing audio for music...")
        confidence = float(settings.get("musicConfidence", 0.15))
        is_music, top_class, top_score = self._detect_music(gained_audio, recording_duration, confidence)
        self._set_status("detected", f"{top_class} ({top_score:.0%})")

        # 3. If music detected, identify via Shazam (uses raw 44.1kHz WAV for clean fingerprint)
        if is_music:
            logger.info("Music detected, identifying via Shazam...")
            self._set_status("identifying", "Music detected! Asking Shazam...")
            song = self._identify_song(raw_wav_bytes)
            if song:
                self._last_song = song
                self._shazam_fail_count = 0
                self._consecutive_misses = 0
                self._last_song_time = time.time()
                self._idle_displayed_at = None
                self._last_weather_fetch = None  # Force fresh weather on next idle
                self._set_status("rendering", f"Found: {song['title']} by {song['artist']}")
                return self._render_song(song, dimensions, settings)

            # Retry only if YAMNet confidence is strong enough to justify the cost
            # (a second recording + Shazam call adds ~7s to the cycle)
            retry_threshold = confidence + 0.10
            if top_score >= retry_threshold:
                self._set_status("no_match", "Shazam: no match. Retrying...")
                logger.info(f"Shazam failed, retrying (YAMNet score {top_score:.0%} >= retry threshold {retry_threshold:.0%})")
                self._set_status("recording", f"Retrying — recording {recording_duration}s...")
                raw_wav_bytes2, _ = self._record_audio(recording_duration)
                self._set_status("identifying", "Retry: asking Shazam again...")
                song = self._identify_song(raw_wav_bytes2)
                if song:
                    self._last_song = song
                    self._shazam_fail_count = 0
                    self._consecutive_misses = 0
                    self._last_song_time = time.time()
                    self._idle_displayed_at = None
                    self._last_weather_fetch = None  # Force fresh weather on next idle
                    self._set_status("rendering", f"Found: {song['title']} by {song['artist']}")
                    return self._render_song(song, dimensions, settings)
            else:
                logger.info(f"Skipping Shazam retry (YAMNet score {top_score:.0%} < retry threshold {retry_threshold:.0%})")

            self._shazam_fail_count = getattr(self, '_shazam_fail_count', 0) + 1
            logger.warning(f"Shazam failed to identify (attempt #{self._shazam_fail_count})")
            self._set_status("unidentified", f"Could not identify song (attempt #{self._shazam_fail_count})")
            return self._render_unidentified(
                dimensions, settings, device_config, top_class, top_score
            )

        # 4. No music detected — apply grace period before showing idle
        self._consecutive_misses += 1
        self._shazam_fail_count = 0
        time_since_song = time.time() - self._last_song_time if self._last_song_time else float('inf')

        if self._last_song and self._consecutive_misses < 3 and time_since_song < 120:
            logger.info(f"No music detected, keeping last song (miss #{self._consecutive_misses}, {time_since_song:.0f}s since last song)")
            self._set_status("idle", f"No music (miss #{self._consecutive_misses}, keeping last song)")
            return None  # Skip display update — keep whatever is currently shown

        logger.info("No music detected, showing idle display")
        self._last_song = None
        self._set_status("idle", f"No music detected ({top_class} {top_score:.0%})")
        return self._render_idle(dimensions, settings, device_config, status_note="No Music Detected")

    # ========== Audio Recording ==========

    def _find_usb_mic_alsa(self):
        """Find USB mic ALSA card number from /proc/asound/cards."""
        try:
            with open("/proc/asound/cards") as f:
                for line in f:
                    if "USB" in line:
                        card_num = line.strip().split()[0]
                        return int(card_num)
        except Exception as e:
            logger.warning(f"Could not read ALSA cards: {e}")
        return None

    def _record_audio(self, recording_duration):
        import subprocess
        import numpy as np
        import wave

        logger.info(f"Recording {recording_duration}s of audio...")

        # Find USB mic ALSA card (cached — doesn't change mid-session)
        if self._cached_mic_card is None:
            card = self._find_usb_mic_alsa()
            if card is not None:
                self._cached_mic_card = card
            else:
                self._cached_mic_card = 1  # Default fallback
                logger.warning("USB mic not found in ALSA cards, defaulting to card 1")

        device = f"plughw:{self._cached_mic_card},0"

        # Configure mic settings once per session (AGC off, volume 12/16)
        if not self._mic_configured:
            logger.debug(f"Configuring ALSA device {device}")
            try:
                subprocess.run(["amixer", "-c", str(self._cached_mic_card), "cset", "numid=4", "off"],
                               capture_output=True, timeout=5)
                subprocess.run(["amixer", "-c", str(self._cached_mic_card), "cset", "numid=3", "12"],
                               capture_output=True, timeout=5)
                self._mic_configured = True
            except Exception as e:
                logger.warning(f"Could not configure mic settings: {e}")

        # Use a secure temp file (unpredictable name, auto-cleaned)
        fd, tmp_wav = tempfile.mkstemp(suffix=".wav", prefix="shazam_")
        os.close(fd)

        try:
            # Record at 44100 Hz 16-bit PCM — full quality for Shazam fingerprinting
            subprocess.run(
                ["arecord", "-D", device, "-f", "S16_LE",
                 "-r", str(RAW_SAMPLE_RATE), "-c", "1",
                 "-d", str(recording_duration), tmp_wav],
                check=True, capture_output=True, timeout=recording_duration + 5
            )

            # Read the raw WAV bytes for Shazam (44.1kHz 16-bit PCM)
            with open(tmp_wav, 'rb') as f:
                raw_wav_bytes = f.read()

            # Read into numpy and downsample to 16kHz for YAMNet
            with wave.open(tmp_wav, 'rb') as wf:
                raw_bytes = wf.readframes(wf.getnframes())
                audio_44k = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        except subprocess.TimeoutExpired:
            raise RuntimeError("Audio recording timed out")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"arecord failed: {e.stderr.decode().strip()}")
        finally:
            # Always clean up temp file
            try:
                os.unlink(tmp_wav)
            except OSError:
                pass

        target_len = int(len(audio_44k) * DOWN_SAMPLE_RATE / RAW_SAMPLE_RATE)
        from scipy.signal import resample
        audio_16k = resample(audio_44k, target_len).astype(np.float32)

        # Gain-boost for YAMNet sensitivity
        max_val = np.max(np.abs(audio_16k))
        if max_val > 0:
            gained = audio_16k / max_val
        else:
            gained = audio_16k.copy()
        gained = np.clip(gained * AUDIO_GAIN, -1.0, 1.0)

        return raw_wav_bytes, gained

    # ========== Music Detection (YAMNet) ==========

    def _init_yamnet(self, recording_duration):
        if self._interpreter is not None and self._recording_duration == recording_duration:
            return

        logger.info("Loading YAMNet TFLite model...")
        try:
            from ai_edge_litert.interpreter import Interpreter
            self._interpreter = Interpreter(model_path=ML_MODEL_PATH)
        except (ModuleNotFoundError, ImportError):
            from tflite_runtime.interpreter import Interpreter
            self._interpreter = Interpreter(model_path=ML_MODEL_PATH)

        input_details = self._interpreter.get_input_details()
        self._waveform_input_index = input_details[0]['index']

        output_details = self._interpreter.get_output_details()
        self._scores_output_index = output_details[0]['index']

        self._interpreter.resize_tensor_input(
            self._waveform_input_index,
            [recording_duration * DOWN_SAMPLE_RATE],
            strict=True
        )
        self._interpreter.allocate_tensors()
        self._recording_duration = recording_duration

        # Load class names
        with open(CLASS_MAP_PATH) as f:
            reader = csv.reader(f)
            self._class_names = [row[2] for row in reader]
            self._class_names = self._class_names[1:]  # Skip header

        logger.info("YAMNet model loaded")

    def _detect_music(self, waveform, recording_duration, confidence_threshold):
        self._init_yamnet(recording_duration)

        self._interpreter.set_tensor(self._waveform_input_index, waveform)
        self._interpreter.invoke()

        scores = self._interpreter.get_tensor(self._scores_output_index)
        scores_mean = scores.mean(axis=0)
        top_i = scores_mean.argmax()
        top_class = self._class_names[top_i]
        top_score = float(scores_mean[top_i])

        logger.info(f"YAMNet top class: '{top_class}' (score: {top_score:.3f})")
        is_music = 'Music' in top_class and top_score > confidence_threshold
        return is_music, top_class, top_score

    # ========== Song Identification (Shazam) ==========

    def _get_event_loop(self):
        """Get or create a reusable asyncio event loop for Shazam calls."""
        if self._event_loop is None or self._event_loop.is_closed():
            self._event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._event_loop)
        return self._event_loop

    def _identify_song(self, raw_wav_bytes):
        from shazamio import Shazam

        # Run Shazam async recognition with raw 44.1kHz 16-bit PCM WAV bytes
        if self._shazam is None:
            self._shazam = Shazam()

        loop = self._get_event_loop()
        try:
            result = loop.run_until_complete(
                asyncio.wait_for(
                    self._shazam.recognize(raw_wav_bytes),
                    timeout=SHAZAM_API_TIMEOUT
                )
            )

            if result and 'track' in result:
                track = result['track']
                album_art = track.get('images', {}).get('coverart')
                song = {
                    'title': track.get('title', 'Unknown'),
                    'artist': track.get('subtitle', 'Unknown'),
                    'album_art': album_art,
                }
                logger.info(f"Identified: {song['title']} by {song['artist']}")
                return song

            # Log why Shazam failed
            if result:
                matches = result.get('matches', [])
                logger.warning(f"Shazam returned no track. matches={len(matches)}, keys={list(result.keys())}")
            else:
                logger.warning("Shazam returned empty result")
            return None
        except asyncio.TimeoutError:
            logger.warning(f"Shazam API timed out after {SHAZAM_API_TIMEOUT}s")
            return None
        except Exception as e:
            logger.error(f"Shazam identification error: {e}", exc_info=True)
            return None

    # ========== Rendering ==========

    def _render_song(self, song, dimensions, settings):
        fit_mode = settings.get("fitMode", "fit")
        pixelated = settings.get("pixelated") == "on"
        pixel_size = int(settings.get("pixelSize", 64))
        led_style = settings.get("pixelStyle", "round") == "round"

        album_art_url = song.get('album_art')
        if album_art_url:
            image = self.image_loader.from_url(
                album_art_url, dimensions, timeout_ms=ALBUM_ART_TIMEOUT_MS, fit_mode=fit_mode
            )
        else:
            image = None

        if not image:
            image = self.image_loader.from_file(
                DEFAULT_IMAGE_PATH, dimensions, fit_mode=fit_mode
            )

        if image:
            if pixelated:
                image = self._apply_pixelated(image, dimensions, pixel_size, led_style)

            # Add title/artist overlay at bottom (unless user disabled it)
            show_info = settings.get("showSongInfo", "on")
            if show_info in ("on", True, "true"):
                image = self._add_title_overlay(
                    image, song['title'], song.get('artist', '')
                )
            # In fit mode (non-pixelated), add rotated label in the left letterbox bar
            show_label = settings.get("showLetterboxLabel") in ("on", True)
            if show_label and fit_mode == 'fit' and not pixelated:
                letterbox_label = settings.get("letterboxLabel", "").strip()
                if letterbox_label == "":
                    letterbox_label = "Now Playing..."
                image = self._add_now_playing_label(image, letterbox_label)

        logger.info("=== ShazamPi Plugin: Song image generated ===")
        return image

    def _apply_pixelated(self, image, dimensions, pixel_size, led_style=True):
        """Downscale album art to pixel_size x pixel_size then render as
        round LED dots on a black canvas to simulate a Tuneshine-style display."""
        width, height = dimensions
        # Use the shorter dimension to make a square pixel grid
        square = min(width, height)

        # Crop to center square from the image
        img_w, img_h = image.size
        if img_w != img_h:
            crop_size = min(img_w, img_h)
            left = (img_w - crop_size) // 2
            top = (img_h - crop_size) // 2
            image = image.crop((left, top, left + crop_size, top + crop_size))

        # Downscale to tiny pixel grid
        tiny = image.resize((pixel_size, pixel_size), Image.LANCZOS)


        if led_style:
            # Draw round LED dots
            canvas = Image.new('RGB', dimensions, (0, 0, 0))
            draw = ImageDraw.Draw(canvas)

            cell_size = square / pixel_size
            dot_radius = cell_size * 0.4  # 80% fill — leaves visible gap between dots
            offset_x = (width - square) // 2
            offset_y = (height - square) // 2

            pixels = tiny.load()
            for py in range(pixel_size):
                for px in range(pixel_size):
                    color = pixels[px, py]
                    # Skip black pixels to save time and keep background clean
                    if isinstance(color, tuple) and sum(color[:3]) < 15:
                        continue
                    cx = offset_x + px * cell_size + cell_size / 2
                    cy = offset_y + py * cell_size + cell_size / 2
                    draw.ellipse(
                        [cx - dot_radius, cy - dot_radius,
                         cx + dot_radius, cy + dot_radius],
                        fill=color
                    )
        else:
            # Square pixel blocks (nearest-neighbor upscale)
            pixelated = tiny.resize((square, square), Image.NEAREST)
            canvas = Image.new('RGB', dimensions, (0, 0, 0))
            offset_x = (width - square) // 2
            offset_y = (height - square) // 2
            canvas.paste(pixelated, (offset_x, offset_y))

        logger.info(f"Applied pixelated effect: {pixel_size}x{pixel_size} grid, led_style={led_style}")
        return canvas

    def _add_now_playing_label(self, image, text="Now Playing..."):
        """Add rotated label text in the left black letterbox bar."""
        img = image.copy()
        width, height = img.size

        # Find the left black bar width by scanning for non-black pixels
        bar_width = 0
        pixels = img.load()
        for x in range(width // 4):
            col_black = all(pixels[x, y] == (0, 0, 0) for y in range(0, height, height // 10))
            if col_black:
                bar_width = x + 1
            else:
                break

        if bar_width < 20:
            return img

        # Find font size that fits text within the display height
        try:
            font_size = max(10, int(bar_width * 0.4))
            font = get_font("Jost", font_size)
            # Shrink until text fits within height
            tmp_img = Image.new('RGBA', (1, 1))
            tmp_draw = ImageDraw.Draw(tmp_img)
            while font_size > 10:
                bbox = tmp_draw.textbbox((0, 0), text, font=font)
                if bbox[2] - bbox[0] <= int(height * 0.75):
                    break
                font_size -= 1
                font = get_font("Jost", font_size)
        except Exception:
            font = ImageFont.load_default()

        # Render text horizontally, then rotate
        tmp = Image.new('RGBA', (height, bar_width), (0, 0, 0, 0))
        tmp_draw = ImageDraw.Draw(tmp)
        text_bbox = tmp_draw.textbbox((0, 0), text, font=font)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]

        tx = (height - text_w) // 2
        ty = (bar_width - text_h) // 2
        tmp_draw.text((tx, ty), text, font=font, fill=(150, 150, 150, 255))

        # Rotate 90° CCW so text reads bottom-to-top
        rotated = tmp.rotate(90, expand=True)

        # Paste onto the left bar area
        img.paste(rotated, (0, 0), rotated)

        return img

    def _render_unidentified(self, dimensions, settings, device_config, top_class, top_score):
        """Render when music is detected but Shazam can't identify it.
        If we have a last song, just re-render it (display stays the same).
        Otherwise show the default/idle image."""
        if self._last_song:
            logger.info("=== ShazamPi Plugin: Unidentified, keeping last song display ===")
            return self._render_song(self._last_song, dimensions, settings)

        logger.info("=== ShazamPi Plugin: Unidentified, no prior song ===")
        return self._render_idle(dimensions, settings, device_config, status_note="Song Not Identified")

    # OWM icon code -> local weather plugin icon file mapping
    # Night codes without a local night variant fall back to the day version
    _ICON_NIGHT_FALLBACK = {
        "03n": "03d", "04n": "04d", "09n": "09d",
        "11n": "11d", "13n": "13d", "50n": "50d",
    }

    def _load_weather_icon(self, icon_code, size=200):
        """Load a weather icon from the weather plugin's local icons directory."""
        weather_icons_dir = os.path.join(
            os.path.dirname(PLUGIN_DIR), "weather", "icons"
        )
        # Try exact code first, then night fallback, then default
        candidates = [icon_code, self._ICON_NIGHT_FALLBACK.get(icon_code), "01d"]
        for code in candidates:
            if code is None:
                continue
            path = os.path.join(weather_icons_dir, f"{code}.png")
            if os.path.exists(path):
                try:
                    icon = Image.open(path).convert("RGBA")
                    icon = icon.resize((size, size), Image.LANCZOS)
                    return icon
                except Exception as e:
                    logger.debug(f"Failed to load weather icon {path}: {e}")
        return None

    def _render_idle(self, dimensions, settings, device_config, status_note="\u266a  Listening for music"):
        # On e-ink, skip redundant idle repaints (slow full-screen flash).
        # Allow a refresh every EINK_IDLE_REFRESH_MINUTES so weather stays current.
        display_type = device_config.get_config("display_type", default="auto")
        if display_type not in ("lcd", "mock") and self._idle_displayed_at is not None:
            elapsed = time.time() - self._idle_displayed_at
            if elapsed < EINK_IDLE_REFRESH_MINUTES * 60:
                logger.info(f"E-ink idle already displayed {elapsed:.0f}s ago, skipping repaint")
                return None
            logger.info(f"E-ink idle refresh due ({elapsed:.0f}s elapsed), repainting")

        width, height = dimensions
        weather = self._get_weather(settings, device_config)

        # Subtle top-to-bottom gradient background (slight blue shift at top)
        gradient = Image.new('RGB', (1, height))
        for y in range(height):
            t = y / max(height - 1, 1)
            r = int(30 + (18 - 30) * t)
            g = int(30 + (18 - 30) * t)
            b = int(38 + (20 - 38) * t)
            gradient.putpixel((0, y), (r, g, b))
        canvas = gradient.resize((width, height), Image.NEAREST)
        del gradient
        draw = ImageDraw.Draw(canvas)

        # Color palette
        color_temp = (241, 122, 36)     # Orange accent for temperature
        color_secondary = (204, 204, 204)  # Light gray for secondary text
        color_white = (255, 255, 255)
        color_subtle = (100, 100, 100)  # Subtle gray for note

        if weather and weather.get('icon_code'):
            self._render_idle_weather(
                canvas, draw, weather, width, height,
                color_temp, color_secondary, color_white, color_subtle, get_font,
                status_note
            )
        else:
            self._render_idle_no_weather(
                canvas, draw, width, height, color_white, color_subtle, get_font,
                status_note
            )

        logger.info("=== ShazamPi Plugin: Idle image generated ===")
        self._idle_displayed_at = time.time()
        return canvas

    def _render_idle_weather(self, canvas, draw, weather, width, height,
                             color_temp, color_secondary, color_white, color_subtle, get_font,
                             status_note="\u266a  Listening for music"):
        """Render idle screen with weather data: icon left, text right, centered."""

        # Fuzzy time at top
        time_str = _fuzzy_time()
        time_size = max(20, int(height * 0.055))
        time_font = get_font("Jost", time_size, "normal") or ImageFont.load_default()
        time_bbox = draw.textbbox((0, 0), time_str, font=time_font)
        time_w = time_bbox[2] - time_bbox[0]
        draw.text(((width - time_w) // 2, int(height * 0.06)), time_str,
                  font=time_font, fill=color_secondary)

        icon_size = min(200, int(height * 0.42))
        icon_img = self._load_weather_icon(weather.get('icon_code', '01d'), icon_size)

        # Font sizes
        temp_size = max(48, int(height * 0.12))
        detail_size = max(22, int(height * 0.05))
        note_size = max(14, int(height * 0.038))

        temp_font = get_font("Jost", temp_size, "bold") or ImageFont.load_default()
        detail_font = get_font("Jost", detail_size, "normal") or ImageFont.load_default()
        note_font = get_font("Jost", note_size, "normal") or ImageFont.load_default()

        # Text lines
        temp_text = weather['temperature']
        feels_text = f"Feels Like {weather.get('feels_like', '')}"
        hilo_text = f"{weather.get('temp_high', '')} / {weather.get('temp_low', '')}"
        desc_text = weather.get('description', '')

        # Measure text block height (use 1.15x multiplier for large font visual height)
        temp_line_h = int(temp_size * 1.15)
        detail_line_h = int(detail_size * 1.15)
        line_gap = int(height * 0.01)

        text_block_h = temp_line_h + (detail_line_h + line_gap) * 3
        icon_actual = icon_img.size[1] if icon_img else 0
        content_h = max(text_block_h, icon_actual)

        # Vertical center for the content block (shifted up slightly)
        content_y = (height - content_h) // 2 - int(height * 0.02)

        # Horizontal layout: icon on left, text on right, both centered as a group
        gap_between = int(width * 0.03)
        icon_w = icon_img.size[0] if icon_img else 0

        # Measure widest text line for total width calc
        text_widths = []
        for text, font in [(temp_text, temp_font), (feels_text, detail_font),
                           (hilo_text, detail_font), (desc_text, detail_font)]:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_widths.append(bbox[2] - bbox[0])
        max_text_w = max(text_widths)

        total_content_w = icon_w + gap_between + max_text_w
        content_x = (width - total_content_w) // 2

        # Draw weather icon with ambient glow
        if icon_img:
            icon_y = content_y + (content_h - icon_actual) // 2

            # Soft orange glow behind icon
            glow_size = int(icon_size * 1.6)
            glow_layer = Image.new('RGBA', (glow_size, glow_size), (0, 0, 0, 0))
            glow_offset = (glow_size - icon_size) // 2
            glow_color_img = Image.new('RGBA', icon_img.size, (241, 122, 36, 35))
            glow_layer.paste(glow_color_img, (glow_offset, glow_offset), mask=icon_img.split()[3])
            glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=icon_size // 4))
            canvas.paste(glow_layer, (content_x - glow_offset, icon_y - glow_offset), glow_layer)
            del glow_color_img, glow_layer

            # Icon on top of glow
            canvas.paste(icon_img, (content_x, icon_y), icon_img)

        # Draw text block (vertically centered within content block)
        text_x = content_x + icon_w + gap_between
        text_y = content_y + (content_h - text_block_h) // 2

        # Temperature
        draw.text((text_x, text_y), temp_text, font=temp_font, fill=color_temp)
        text_y += temp_line_h

        # Feels like
        draw.text((text_x, text_y), feels_text, font=detail_font, fill=color_secondary)
        text_y += detail_line_h + line_gap

        # Hi / Lo
        draw.text((text_x, text_y), hilo_text, font=detail_font, fill=color_secondary)
        text_y += detail_line_h + line_gap

        # Description
        draw.text((text_x, text_y), desc_text, font=detail_font, fill=color_white)

        # Thin accent separator
        line_y = height - int(height * 0.13)
        line_w = int(width * 0.4)
        line_x = (width - line_w) // 2
        line_thickness = max(1, int(width * 0.003))
        draw.line([(line_x, line_y), (line_x + line_w, line_y)],
                  fill=color_subtle, width=line_thickness)

        # Status note at bottom center
        note_bbox = draw.textbbox((0, 0), status_note, font=note_font)
        note_w = note_bbox[2] - note_bbox[0]
        draw.text(
            ((width - note_w) // 2, height - int(height * 0.08)),
            status_note, font=note_font, fill=color_subtle
        )

    def _render_idle_no_weather(self, canvas, draw, width, height,
                                color_white, color_subtle, get_font,
                                status_note="\u266a  Listening for music"):
        """Render idle screen when no weather data is available."""

        # Fuzzy time at top
        color_secondary = (204, 204, 204)
        time_str = _fuzzy_time()
        time_size = max(20, int(height * 0.055))
        time_font = get_font("Jost", time_size, "normal") or ImageFont.load_default()
        time_bbox = draw.textbbox((0, 0), time_str, font=time_font)
        time_w = time_bbox[2] - time_bbox[0]
        draw.text(((width - time_w) // 2, int(height * 0.06)), time_str,
                  font=time_font, fill=color_secondary)

        main_size = max(36, int(height * 0.08))
        note_size = max(14, int(height * 0.038))

        main_font = get_font("Jost", main_size, "bold") or ImageFont.load_default()
        note_font = get_font("Jost", note_size, "normal") or ImageFont.load_default()

        main_text = "Listening..."

        main_bbox = draw.textbbox((0, 0), main_text, font=main_font)
        main_w = main_bbox[2] - main_bbox[0]

        # Center the "Listening..." text
        draw.text(
            ((width - main_w) // 2, (height // 2) - int(height * 0.04)),
            main_text, font=main_font, fill=color_white
        )

        # Thin accent separator
        line_y = height - int(height * 0.13)
        line_w = int(width * 0.4)
        line_x = (width - line_w) // 2
        line_thickness = max(1, int(width * 0.003))
        draw.line([(line_x, line_y), (line_x + line_w, line_y)],
                  fill=color_subtle, width=line_thickness)

        # Status note at bottom center
        note_bbox = draw.textbbox((0, 0), status_note, font=note_font)
        note_w = note_bbox[2] - note_bbox[0]
        draw.text(
            ((width - note_w) // 2, height - int(height * 0.08)),
            status_note, font=note_font, fill=color_subtle
        )

    # ========== Weather ==========

    # WMO weather code to icon code mapping (matches Weather plugin's Open-Meteo icons)
    _WMO_TO_ICON = {
        0: "01",   # Clear sky
        1: "022",  # Mainly clear
        2: "02",   # Partly cloudy
        3: "04",   # Overcast
        45: "50",  # Fog
        48: "48",  # Icy fog
        51: "51", 53: "53", 55: "09",  # Drizzle
        56: "56", 57: "57",            # Freezing drizzle
        61: "51", 63: "53", 65: "09",  # Rain
        66: "56", 67: "57",            # Freezing rain
        71: "71", 73: "73", 75: "13",  # Snow
        77: "77",                       # Snow grains
        80: "51", 81: "53", 82: "09",  # Showers
        85: "71", 86: "13",            # Snow showers
        95: "11", 96: "11", 99: "11",  # Thunderstorm
    }

    # WMO weather code to description mapping
    _WMO_DESCRIPTIONS = {
        0: "Clear Sky", 1: "Mainly Clear", 2: "Partly Cloudy", 3: "Overcast",
        45: "Foggy", 48: "Icy Fog",
        51: "Light Drizzle", 53: "Moderate Drizzle", 55: "Heavy Drizzle",
        56: "Light Freezing Drizzle", 57: "Freezing Drizzle",
        61: "Light Rain", 63: "Moderate Rain", 65: "Heavy Rain",
        66: "Light Freezing Rain", 67: "Freezing Rain",
        71: "Light Snow", 73: "Moderate Snow", 75: "Heavy Snow", 77: "Snow Grains",
        80: "Light Showers", 81: "Moderate Showers", 82: "Heavy Showers",
        85: "Light Snow Showers", 86: "Heavy Snow Showers",
        95: "Thunderstorm", 96: "Thunderstorm with Hail", 99: "Thunderstorm with Heavy Hail",
    }

    def _find_weather_settings(self, device_config):
        """Search loop config for a weather plugin instance and borrow its location settings."""
        try:
            for loop in device_config.loop_manager.loops:
                for plugin_ref in loop.plugin_order:
                    if plugin_ref.plugin_id == "weather" and plugin_ref.plugin_settings:
                        ws = plugin_ref.plugin_settings
                        lat = ws.get("latitude", "")
                        lon = ws.get("longitude", "")
                        if lat and lon:
                            logger.info("Borrowing weather settings from Weather plugin")
                            return {
                                "geoCoordinates": f"{lat}, {lon}",
                                "units": ws.get("units", "imperial"),
                            }
        except Exception as e:
            logger.debug(f"Could not read weather plugin settings: {e}")
        return None

    def _get_weather(self, settings, device_config):
        """Fetch current weather + daily high/low from Open-Meteo (free, no API key).

        Uses Open-Meteo's current weather and daily forecast endpoints to get
        accurate daily high/low temperatures (not OWM's observation-range values).
        """
        coords = settings.get("geoCoordinates", "").strip()

        # If no local weather config, try borrowing from weather plugin
        if not coords:
            borrowed = self._find_weather_settings(device_config)
            if borrowed:
                coords = borrowed["geoCoordinates"]
                settings = {**settings, "units": borrowed.get("units", settings.get("units", "imperial"))}

        if not coords:
            return self._last_weather

        # Use cache if fresh enough
        if (self._last_weather and self._last_weather_fetch and
                datetime.datetime.now() - self._last_weather_fetch <
                datetime.timedelta(minutes=WEATHER_CACHE_MINUTES)):
            return self._last_weather

        units = settings.get("units", "imperial")
        try:
            lat, lon = [x.strip() for x in coords.split(',')]

            # Open-Meteo: current temp/feels-like/weather-code + today's daily high/low
            temp_unit_param = "fahrenheit" if units == "imperial" else "celsius"
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,apparent_temperature,weather_code,is_day"
                f"&daily=temperature_2m_max,temperature_2m_min"
                f"&temperature_unit={temp_unit_param}"
                f"&forecast_days=1&timezone=auto"
            )

            from utils.http_client import get_http_session
            session = get_http_session()
            response = session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            current = data.get("current", {})
            daily = data.get("daily", {})

            temp_symbol = '\u00b0F' if units == "imperial" else '\u00b0C'
            temperature = str(round(current.get("temperature_2m", 0))) + temp_symbol
            feels_like = str(round(current.get("apparent_temperature", 0))) + temp_symbol

            # Daily high/low from forecast (accurate, not OWM observation range)
            highs = daily.get("temperature_2m_max", [])
            lows = daily.get("temperature_2m_min", [])
            temp_high = str(round(highs[0])) + temp_symbol if highs else temperature
            temp_low = str(round(lows[0])) + temp_symbol if lows else temperature

            # Map WMO weather code to icon and description
            weather_code = current.get("weather_code", 0)
            is_day = current.get("is_day", 1)
            icon_stem = self._WMO_TO_ICON.get(weather_code, "01")
            icon_code = icon_stem + ("d" if is_day else "n")
            # Only certain icons have night variants in the icon set
            if not is_day and icon_stem not in ("01", "022", "02"):
                icon_code = icon_stem + "d"  # Fall back to day version

            condition = self._WMO_DESCRIPTIONS.get(weather_code, "Unknown")

            self._last_weather = {
                'temperature': temperature,
                'feels_like': feels_like,
                'temp_high': temp_high,
                'temp_low': temp_low,
                'description': condition,
                'icon_code': icon_code,
                'weather_sub_description': f"Feels like {feels_like}. {condition}",
            }
            self._last_weather_fetch = datetime.datetime.now()
            return self._last_weather

        except Exception as e:
            logger.error(f"Weather fetch error: {e}")
            return self._last_weather

    # ========== Title Overlay ==========

    def _add_title_overlay(self, image, title, subtitle=""):
        img = image.copy()
        draw = ImageDraw.Draw(img, 'RGBA')
        width, height = img.size

        # Font sizing
        try:
            title_font_size = max(16, int(height * 0.03))
            subtitle_font_size = max(14, int(height * 0.022))
            title_font = get_font("Jost", title_font_size, "bold")
            subtitle_font = get_font("Jost", subtitle_font_size)
        except Exception:
            title_font = ImageFont.load_default()
            subtitle_font = title_font
            logger.warning("Could not load custom font, using default")

        padding = max(10, int(height * 0.012))

        # Measure text
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_h = title_bbox[3] - title_bbox[1]
        title_w = title_bbox[2] - title_bbox[0]

        subtitle_h = 0
        subtitle_w = 0
        if subtitle:
            sub_bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
            subtitle_h = sub_bbox[3] - sub_bbox[1]
            subtitle_w = sub_bbox[2] - sub_bbox[0]

        # Calculate overlay height
        total_text_h = title_h + (subtitle_h + padding // 2 if subtitle else 0)
        bar_top = height - total_text_h - padding * 2

        # Draw semi-transparent background bar
        draw.rectangle(
            [(0, bar_top), (width, height)],
            fill=(0, 0, 0, 180)
        )

        # Draw title (centered)
        title_x = (width - title_w) // 2
        title_y = bar_top + padding
        self._draw_outlined_text(draw, title_x, title_y, title, title_font)

        # Draw subtitle (centered, below title)
        if subtitle:
            sub_x = (width - subtitle_w) // 2
            sub_y = title_y + title_h + padding // 2
            self._draw_outlined_text(draw, sub_x, sub_y, subtitle, subtitle_font)

        return img

    def _draw_outlined_text(self, draw, x, y, text, font, outline=2):
        for dx in range(-outline, outline + 1):
            for dy in range(-outline, outline + 1):
                if dx != 0 or dy != 0:
                    draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
