# DashPi Fork - Complete Comparison from Original

**Original Repository:** [fatihak/DashPi](https://github.com/fatihak/DashPi)
**Upstream Commit:** `fb71cc3` (Handle background image color for grayscale images)
**Fork Date:** February 2026
**Current Version:** v2.1.0+

---

## Executive Summary

This fork replaces DashPi's complex **Playlist system** with a simpler **Loop system**. The primary goals were **simplicity**, **stability**, and **enhanced functionality** for personal use on a Raspberry Pi Zero with Inky Impression 7.3" display.

| Aspect | Original (fatihak) | This Fork |
|--------|-------------------|-----------|
| Display System | Playlists (complex) | Loops (simple) |
| Plugin Config | Per-instance settings, names, schedules | Simple plugin references |
| Stock Tracking | Not available | Full-featured Stocks plugin |
| Auto-Refresh | Tied to playlist rotation | Independent per-plugin |
| OOM Protection | Basic | 20MP limit with graceful skip |
| Main Page UI | Playlist-focused | Compact dashboard, skip button |

**Impact:** 77 files changed | +5,950 lines | -1,938 lines

---

## What the Original DashPi Had

### Display System
- **Playlists only:** Time-windowed collections with per-plugin instance settings
- **PluginInstance class:** Each plugin in a playlist had its own name, custom settings, refresh schedule
- **PlaylistManager:** Managed multiple playlists with priority/overlap rules
- **Complex scheduling:** Multiple playlists could be active, priority determined by time window size

### Main Page
- Playlist-focused navigation
- Basic countdown display
- No skip functionality
- No quick loop enable/disable

### Plugins
- Standard set: Weather, Clock, Calendar, AI Image, AI Text, APOD, Wikipedia POTD, RSS, etc.
- No stock market plugin
- Image plugins used crop mode by default

---

## What This Fork Changes

### 1. New Loop System (Replaced Playlists)

**Removed:**
- `src/blueprints/playlist.py` - All playlist CRUD endpoints
- `src/templates/playlist.html` - Playlist management UI
- `PlaylistManager` class - Playlist collection manager
- `Playlist` class - Individual playlist with time windows
- `PluginInstance` class - Complex plugin configuration with per-instance names/settings

**Added (New):**
- `src/blueprints/loops.py` - New loop management API
- `src/templates/loops.html` - Loop configuration UI
- `Loop` class - Simple time-windowed rotation with random mode support
- `LoopManager` class - Manages multiple loops
- `PluginReference` class - Simplified plugin tracking (just ID + refresh interval)
- `migrate_playlists_to_loops.py` - Migration script for existing users

**Why:** The playlist system required naming each plugin instance, configuring individual settings per instance, and managing complex refresh schedules. Loops provide the same time-based rotation with much simpler configuration - just add plugins to a loop and set a global rotation interval.

---

### 2. New Stocks Plugin

**Location:** `src/plugins/stocks/`

**Features:**
- Real-time stock quotes via `yfinance` library
- Persistent ticker list with company names
- Ticker validation (verifies symbol exists before adding)
- Drag-and-drop sortable list in UI
- Configurable auto-refresh interval (1-60 minutes)
- Dynamic font scaling based on ticker count
- Color-coded up/down arrows (green/red)
- Footer with last update timestamp
- Settings survive service restarts

**Files:**
- `stocks.py` - Plugin logic
- `settings.html` - Configuration UI with ticker management
- `render/stocks.html` - Display template
- `render/stocks.css` - Styling
- `icon.png` - Plugin icon
- `plugin-info.json` - Metadata

---

### 3. Auto-Refresh System

**Original Behavior:**
- Plugins only refreshed when playlist rotation occurred
- No way to keep a plugin updating while staying on screen

**New Behavior:**
- Plugins can specify independent refresh intervals
- Auto-refresh works even when loop rotation is disabled
- Example: Stocks updates every 5 minutes while loop is paused on that plugin

**Implementation:**
- `auto_refresh_tracking` stored in device config
- `_get_auto_refresh_seconds()` checks plugin settings
- `_should_auto_refresh()` evaluates timing independently
- Settings persist across service restarts

---

### 4. OOM Crash Protection

**Problem:** Large images (e.g., 57MP Wikipedia POTD) crashed Pi Zero

**Solution in `src/utils/image_loader.py`:**
```python
MAX_MEGAPIXELS_LOW_RESOURCE = 20  # Pi Zero (512MB)
MAX_MEGAPIXELS_HIGH_RESOURCE = 100  # Pi 3/4 (1-4GB)
```

**Behavior:**
- Checks image dimensions before full load
- Gracefully skips oversized images with error log
- Prevents OOM crashes and service restarts

---

### 5. Main Page UI Redesign

**Status Dashboard (Before):**
- Mode toggle button
- Conditional display based on mode
- Basic countdown
- No quick actions

**Status Dashboard (After):**
- Fixed 65%/35% layout split
- Left section: Loop toggle, Current plugin, Next plugin
- Right section: Countdown label, time/status, Skip button
- Plugin names truncate with ellipsis when needed
- Vertical divider separates sections

**Skip Button:**
- Advances to next plugin immediately
- Works with sequential and random modes
- Polls for UI updates after skip
- Button anchored to far right (absolute positioning)

**Random Mode "Next" Display:**
- Pre-computes next plugin selection
- `next_plugin_index` stored in Loop class
- `peek_next_plugin()` returns upcoming plugin without advancing
- UI shows actual next plugin, not "Random"

**Sync Improvements:**
- Status bar refreshes when new image detected
- Countdown stays synchronized with display changes

---

### 6. Plugin Enhancements

**All Image Plugins:**
- Default to letterbox (fit) mode instead of crop
- Better aspect ratio preservation

**AI Image Plugin (`src/plugins/ai_image/`):**
- Enhanced settings UI
- Improved error handling
- Letterbox mode support

**AI Text Plugin (`src/plugins/ai_text/`):**
- Improved rendering
- Better settings organization

**APOD Plugin (`src/plugins/apod/`):**
- Enhanced image handling
- Letterbox mode support
- Additional settings

**Weather Plugin (`src/plugins/weather/`):**
- Reorganized CSS
- More display options
- Enhanced settings

**Wikipedia POTD Plugin (`src/plugins/wpotd/`):**
- Better image fetching
- Handles oversized images gracefully

**Unsplash Plugin (`src/plugins/unsplash/`):**
- Fixed missing `requests` import

---

### 7. Stability Improvements

**Systemd Service (on Pi, not in repo):**
```ini
Restart=always
RestartSec=10
MemoryMax=350M
```

**Performance Optimizations:**
- Cached time range calculations in `Loop.get_time_range_minutes()`
- Cached active loop determination in `LoopManager`
- Reduced redundant config file reads

---

### 8. Configuration Changes

**Removed from config:**
- `display_mode` - Always loop mode now
- `playlist_config` - No playlists

**Added to config:**
- `auto_refresh_tracking` - Persists auto-refresh state
- `stocks_plugin_settings` - Stocks plugin persistence

**API Keys:**
- Stored in `.env` file on Pi
- Must exclude from rsync deployments

---

## File Change Summary

### Added Files
| File | Purpose |
|------|---------|
| `src/blueprints/loops.py` | Loop management API |
| `src/templates/loops.html` | Loop configuration UI |
| `src/plugins/stocks/*` | Complete stocks plugin |
| `migrate_playlists_to_loops.py` | Migration script |
| `VERSION` | Version tracking |

### Removed Files
| File | Reason |
|------|--------|
| `src/blueprints/playlist.py` | Playlists removed |
| `src/templates/playlist.html` | Playlists removed |

### Significantly Modified Files
| File | Changes |
|------|---------|
| `src/model.py` | Removed playlist classes, enhanced Loop with random/peek |
| `src/refresh_task.py` | Auto-refresh system, loop-only logic |
| `src/templates/dash.html` | Complete dashboard redesign |
| `src/static/styles/main.css` | New dashboard styles |
| `src/utils/image_loader.py` | OOM protection |
| `src/blueprints/main.py` | Skip API, enhanced next_change_time API |
| `src/config.py` | Simplified, removed playlist methods |
| `src/dashpi.py` | Updated blueprint registration |

---

## Deployment Requirements

**Additional Python Package:**
```bash
sudo /usr/local/dashpi/venv_dashpi/bin/pip install yfinance
```

**Critical Deployment Note:**
```bash
# Must exclude .env to preserve API keys
rsync --exclude='.env' ...
```

---

## Version History

| Version | Date | Summary |
|---------|------|---------|
| v2.0.0 | Feb 2026 | Major refactor: Unified Loop system, removed playlists |
| v2.1.0 | Feb 2026 | Stocks plugin, auto-refresh, UI improvements |
| v2.1.0+ | Feb 9, 2026 | Skip button, OOM protection, dashboard layout fixes |
