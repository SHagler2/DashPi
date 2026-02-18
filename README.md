# DashPi

## About DashPi
DashPi is an LCD display dashboard powered by a Raspberry Pi, forked from [InkyPi](https://github.com/fatihak/InkyPi). It drives a Waveshare 7" 1024x600 HDMI IPS display via framebuffer output.

DashPi retains InkyPi's plugin ecosystem and web UI, adapted for LCD output instead of e-ink.

**Features**:
- Web Interface allows you to update and configure the display from any device on your network
- Full-color LCD output at 1024x600 resolution
- Open source project allowing you to modify, customize, and create your own plugins
- Set up scheduled loops to display different plugins at designated times

**Plugins**:

- Image Upload: Upload and display any image from your browser
- Daily Newspaper/Comic: Show daily comics and front pages of major newspapers from around the world
- Clock: Customizable clock faces for displaying time
- AI Image/Text: Generate images and dynamic text from prompts using OpenAI's models
- Weather: Display current weather conditions and multi-day forecasts with a customizable layout
- Calendar: Visualize your calendar from Google, Outlook, or Apple Calendar with customizable layouts

For documentation on building custom plugins, see [Building DashPi Plugins](./docs/building_plugins.md).

## Hardware
- Raspberry Pi (4 | 3 | Zero 2 W)
- MicroSD Card (min 8 GB)
- Waveshare 7" 1024x600 HDMI IPS Display

## Installation
To install DashPi, follow these steps:

1. Clone the repository:
    ```bash
    git clone <repository-url> DashPi
    ```
2. Navigate to the project directory:
    ```bash
    cd DashPi
    ```
3. Run the installation script with sudo:
    ```bash
    sudo bash install/install.sh
    ```

After the installation is complete, the script will prompt you to reboot your Raspberry Pi. Once rebooted, the display will update to show the DashPi splash screen.

## Update
To update your DashPi with the latest code changes:
1. Navigate to the project directory:
    ```bash
    cd DashPi
    ```
2. Fetch the latest changes from the repository:
    ```bash
    git pull
    ```
3. Run the update script with sudo:
    ```bash
    sudo bash install/update.sh
    ```

## Uninstall
To uninstall DashPi:

```bash
sudo bash install/uninstall.sh
```

## License

Distributed under the GPL 3.0 License, see [LICENSE](./LICENSE) for more information.

This project includes fonts and icons with separate licensing and attribution requirements. See [Attribution](./docs/attribution.md) for details.

## Acknowledgements

DashPi is a fork of [InkyPi](https://github.com/fatihak/InkyPi) by fatihak.
