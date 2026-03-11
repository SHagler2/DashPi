# Changelog

All notable changes to DashPi are documented here.

## [Unreleased]

### Fixed
- Config import race condition — loops imported via web UI were silently lost on restart because `write_config()` serialized the old in-memory `loop_manager` before the new one was built. Fixed by updating `device_config.config` directly, rebuilding `loop_manager`, then calling `write_config()` in the correct order.
- Stocks plugin "Last Updated" timestamp showed wrong time when device timezone differs from Eastern (hardcoded `America/New_York` replaced with device's configured timezone).
- Invalid timezone strings in stocks plugin and brightness scheduler no longer crash; both fall back to UTC with a warning.

### Added
- GitHub Actions CI workflow — runs full pytest suite on every push/PR to main.
- Critical path tests for `RefreshTask` — unit tests for `ManualRefresh`, `AutoRefresh`, `LoopRefresh` action classes; end-to-end smoke test using real Clock plugin through mock display.

---

## [2.1.0] — 2026-03-05

Full codebase audit across 24 files. Focus: reliability, security, and performance.

### Plugins
- **All plugins**: API call timeouts added to every external HTTP request.
- **Weather, Stocks, Calendar**: Crash-on-None guards; dead code removal.
- **APOD, Unsplash, WPOTD, Art Museum**: HTTP→HTTPS upgrades; standard-res images to prevent OOM on Pi Zero.
- **AI Image / AI Text**: `stroke_width` replaces 24-call outline loops; prompt length capped.
- **Comic, Image Album, GitHub Stars**: Input validation; crash guards.
- **Clock**: Outline drawing optimized.
- **ISS Tracker**: Memory pressure fix (Pi Zero hangs); 2-second refresh removed to prevent SD card I/O stalls; max elevation added to pass info; miles fix in imperial mode.
- **Newspaper, Todo List**: Reliability hardening.
- **Flight Tracker**: Parallel API calls; 15s timeout; input validation; emergency squawk highlighting.
- **ShazamPi**: Performance and reliability overhaul; weather fallback switched to Open-Meteo; fuzzy word clock on idle screen; weather cache invalidated on song detection.
- **Plugin Registry**: Import safety improvements.

### Core
- `http_client`: Retry object with backoff on 5xx errors; GET-only retry policy.
- `refresh_task`: `manual_update()` capped at 120s timeout to prevent indefinite blocking.
- `config`: Atomic write fallback with error logging.
- `display_manager`: Dict copy to avoid shared state mutation; brightness value bounds clamped.

### Blueprints
- `loops`: `request.json` null guard on all 11 endpoints.
- `plugin`: yfinance ticker lookup wrapped in `ThreadPoolExecutor` with 15s timeout.
- `settings`: Float bounds clamping; ZIP bomb guard (128MB uncompressed limit).

### Fixed
- Config import losing loops when service is running (partial fix; fully resolved in Unreleased).
- `git safe.directory` added to install script for self-update to work when service runs as root.
- AI image prompt randomizer defaulting to surrealism/Dalí — temperature raised to 2.0, style diversity instruction added.
- Checkbox settings (`showTitle` etc.) not persisting when saved to a loop.
- Loop plugin settings merge order overwriting user preferences.

### Added
- Bootstrap one-liner install script (`install/bootstrap.sh`).
- Weighted random loop selection.
- Version number in backup filenames.

---

## [2.0.0] — 2026-02-15

Major release: DashPi and InkyPi unified into a single codebase with automatic display detection.

### Added
- **Multi-display support**: Single codebase runs on both LCD (Waveshare) and e-ink (Pimoroni Inky) displays.
- **Display auto-detection**: Inky detected via I2C first, then LCD via framebuffer, then mock.
- **WiFi provisioning**: Hotspot + captive portal for headless setup. SSID `{device-name}-Setup`, password `dashpisetup`. Android/iOS/Windows detection and auto-redirect. Safe AP mode (never disconnects WiFi first).
- **Crossfade transitions**: Smooth 10-frame crossfade between plugin rotations on LCD (smoothstep easing, 800ms). Toggle in Settings. E-ink skipped.
- **Config export/import**: ZIP backup/restore on Settings page. Includes `device.json`, optional `.env`, optional saved images. Validates on import, backs up to `.bak`. Upload limit 64MB.
- **Dashboard brightness slider**: Temporary override that auto-reverts on next brightness schedule period. Instant LCD feedback via `reapply_brightness()`.
- **Evening brightness period**: Three-period scheduling (day/evening/night).
- **Diagnostics page**: Live system metrics with memory and swap rolling charts.
- **Self-update**: Branch-aware `git pull` from Settings page.
- **Native Gemini image models**: `gemini-2.5-flash-image`, `gemini-3-pro-image-preview`, `gemini-3.1-flash-image-preview` via `generate_content()`.
- **Market status indicator**: Open/closed badge in stocks plugin.
- **ISS real-time updates**: Live pass tracking with city name fallback.
- **Flight tracker plugin**: Real-time ADS-B tracking via ADS-B Exchange.
- **Loop override / pin plugin**: Force-display a specific plugin bypassing rotation.
- **Auto-refresh per plugin**: Per-plugin configurable refresh interval.
- **Docstrings**: Full pass across all 43 source files.
- **Favicon**.
- **Plugin icon overlay**: Optional plugin icon shown in corner of display image.

### Security
- CSP headers on all responses.
- SSRF filtering (`_validate_url`) in image loader.
- File upload mimetype validation via `PIL.Image.verify()`.
- `/check_files` restricted to saved images directory (was a filesystem oracle).
- XSS in `onclick` handlers fixed (`|tojson` filter).
- `MAX_CONTENT_LENGTH` set to 16MB (raised to 64MB for config import).
- `secure_filename()` for file uploads.
- Log download capped at 168 hours.
- Error messages sanitized.

### Fixed
- Web UI image not auto-refreshing after plugin changes (cache-busting `?t=timestamp` replaces fragile blob URL pipeline).
- `.env` path resolution through install symlinks.
- Install script: `wait_for_apt()` prevents dpkg lock races; batched pip install for low-memory Pi Zero; swap expanded to 2GB; `spi0-0cs` overlay for kernel 6.x+.
- Self-update using hardcoded `main` branch (now uses current branch).
- ISS tracker city label falling back to weather plugin city name.
- WiFi captive portal on netplan systems.

---

## [1.0] — 2025

Initial public release of DashPi — LCD display dashboard for Raspberry Pi.

### Included at launch
- Plugin loop rotation with configurable interval.
- Plugins: Clock, Weather, Stocks, Calendar, APOD, Unsplash, WPOTD, Art Museum, AI Image, AI Text, Comic, Image Album, GitHub Stars, ISS Tracker, Newspaper, Todo List, ShazamPi, Flight Tracker.
- Web UI for configuration (Flask + Waitress).
- Basic brightness day/night scheduling.
- Boot splash screen.
- Test suite (`test_plugins_api.py`, `test_plugins_file.py`).
