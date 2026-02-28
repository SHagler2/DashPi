# DashPi

## About DashPi

DashPi is an open-source, customizable smart display powered by a Raspberry Pi. It supports **multiple display types** with automatic hardware detection — just install and boot, and DashPi figures out the rest.

| Display Type | What You Get |
|---|---|
| **LCD** (e.g. Waveshare 7" HDMI IPS) | Full-color, fast refresh, touch-capable, backlight with brightness scheduling |
| **E-Ink** (Pimoroni Inky) | Paper-like aesthetic, no glare, ultra-low power, auto-detected via Inky library |
| **E-Ink** (Waveshare e-Paper) | Wide range of sizes and color options, including bi-color (black/red) displays |

All display types share the same plugin ecosystem, web UI, and configuration — only the rendering differs. DashPi auto-detects your hardware on first boot and adapts accordingly.

**Features**:
- **Auto-detection**: Plug in any supported display and DashPi configures itself
- **Web Interface**: Configure the display, manage plugins, and set up loops from any device on your network
- **24 built-in plugins**: Weather, clocks, AI-generated images, news, stocks, art museum, ISS tracker, and more
- **Scheduled loops**: Display different plugins at different times of day
- **Multi-device friendly**: Run multiple DashPi devices on the same network, each with its own name and settings (accessible via `hostname.local`)
- **Self-update**: Check for and apply updates directly from the web UI
- **Open source**: Modify, customize, and create your own plugins

**Plugins include**: Weather, Clock, AI Image, AI Text, NASA APOD, Art Museum, Stocks, ISS Tracker, Flight Tracker, ShazamPi, Calendar, Newspaper, Comics, RSS, Image Upload, Image Album, Image URL, Countdown, GitHub, To-Do List, Unsplash, Year Progress, Wikipedia POTD, and more.

For documentation on building custom plugins, see [Building Plugins](./docs/building_plugins.md).

## Hardware

- Raspberry Pi (4 | 3 | Zero 2 W)
- MicroSD Card (min 8 GB)
- One of the following displays:
    - **LCD**: Waveshare 7" 1024x600 HDMI IPS Display (touch supported)
    - **Inky e-Paper**: Pimoroni Inky Impression (13.3", 7.3", 5.7", 4") or Inky wHAT (4.2")
    - **Waveshare e-Paper**: Spectra 6, Black and White, bi-color, various sizes
        - Note: IT8951-based displays are not supported

## Installation

1. Clone the repository:
    ```bash
    git clone https://github.com/SHagler2/DashPi.git
    ```
2. Navigate to the project directory:
    ```bash
    cd DashPi
    ```
3. Run the installation script with sudo:
    ```bash
    sudo bash install/install.sh
    ```

After the installation is complete, the script will prompt you to reboot your Raspberry Pi. Once rebooted, DashPi will auto-detect your display and show the startup screen.

**Note**: The installation script requires sudo privileges. We recommend starting with a fresh installation of Raspberry Pi OS. The installer automatically enables SPI and I2C interfaces when needed.

## Update

DashPi can be updated directly from the **Settings** page in the web UI — just click "Check for Updates."

Or update manually:
```bash
cd DashPi
git pull
sudo bash install/install.sh
```

## Uninstall

```bash
sudo bash install/uninstall.sh
```

## Coming from InkyPi?

DashPi v2.0 is the successor to [InkyPi](https://github.com/SHagler2/InkyPi). Your e-ink hardware is fully supported — DashPi auto-detects Inky and Waveshare displays on first boot. Your existing plugins, loops, and API keys can be migrated by copying your `device.json` config and `.env` file to the new installation.

## License

Distributed under the GPL 3.0 License, see [LICENSE](./LICENSE) for more information.

This project includes fonts and icons with separate licensing and attribution requirements. See [Attribution](./docs/attribution.md) for details.

## Acknowledgements

DashPi is a fork of [InkyPi](https://github.com/fatihak/InkyPi) by fatihak.
