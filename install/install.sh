#!/bin/bash

# =============================================================================
# Script Name: install.sh
# Description: This script automates the installation of DashPi and creation of
#              the DashPi service.
#
# Usage: ./install.sh
# =============================================================================

# Formatting stuff
bold=$(tput bold)
normal=$(tput sgr0)
red=$(tput setaf 1)
green=$(tput setaf 2)

SOURCE=${BASH_SOURCE[0]}
while [ -h "$SOURCE" ]; do # resolve $SOURCE until the file is no longer a symlink
  DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )
  SOURCE=$(readlink "$SOURCE")
  [[ $SOURCE != /* ]] && SOURCE=$DIR/$SOURCE
done
SCRIPT_DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )

# Use hostname as app/service name if it's 'inkypi', otherwise default to 'dashpi'
HOSTNAME_LOWER=$(hostname | tr '[:upper:]' '[:lower:]')
if [ "$HOSTNAME_LOWER" = "inkypi" ]; then
  APPNAME="inkypi"
else
  APPNAME="dashpi"
fi
INSTALL_PATH="/usr/local/$APPNAME"
SRC_PATH="$SCRIPT_DIR/../src"
BINPATH="/usr/local/bin"
VENV_PATH="$INSTALL_PATH/venv_$APPNAME"

SERVICE_FILE="$APPNAME.service"
SERVICE_FILE_SOURCE="$SCRIPT_DIR/$SERVICE_FILE"
SERVICE_FILE_TARGET="/etc/systemd/system/$SERVICE_FILE"

APT_REQUIREMENTS_FILE="$SCRIPT_DIR/debian-requirements.txt"
PIP_REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"

check_permissions() {
  # Ensure the script is run with sudo
  if [ "$EUID" -ne 0 ]; then
    echo_error "ERROR: Installation requires root privileges. Please run it with sudo."
    exit 1
  fi
}

show_loader() {
  local pid=$!
  local delay=0.1
  local spinstr='|/-\'
  printf "$1 [${spinstr:0:1}] "
  while ps a | awk '{print $1}' | grep -q "${pid}"; do
    local temp=${spinstr#?}
    printf "\r$1 [${temp:0:1}] "
    spinstr=${temp}${spinstr%"${temp}"}
    sleep ${delay}
  done
  if [[ $? -eq 0 ]]; then
    printf "\r$1 [\e[32m\xE2\x9C\x94\e[0m]\n"
  else
    printf "\r$1 [\e[31m\xE2\x9C\x98\e[0m]\n"
  fi
}

echo_success() {
  echo -e "$1 [\e[32m\xE2\x9C\x94\e[0m]"
}

echo_override() {
  echo -e "\r$1"
}

echo_header() {
  echo -e "${bold}$1${normal}"
}

echo_error() {
  echo -e "${red}$1${normal} [\e[31m\xE2\x9C\x98\e[0m]\n"
}

echo_blue() {
  echo -e "\e[38;2;65;105;225m$1\e[0m"
}


install_debian_dependencies() {
  if [ -f "$APT_REQUIREMENTS_FILE" ]; then
    sudo apt-get update > /dev/null &
    show_loader "Fetch available system dependencies updates. "

    xargs -a "$APT_REQUIREMENTS_FILE" sudo apt-get install -y > /dev/null &
    show_loader "Installing system dependencies. "
  else
    echo "ERROR: System dependencies file $APT_REQUIREMENTS_FILE not found!"
    exit 1
  fi
}

# Get total system RAM in MB
get_total_ram_mb() {
  awk '/MemTotal/ {printf "%d", $2 / 1024}' /proc/meminfo
}

is_low_memory() {
  local ram_mb
  ram_mb=$(get_total_ram_mb)
  [ "$ram_mb" -lt 1024 ]
}

setup_swap() {
  # On low-memory devices (Pi Zero, < 1GB RAM), expand swap to prevent OOM
  # during pip install. This is safe to run on all devices.
  if is_low_memory; then
    local ram_mb
    ram_mb=$(get_total_ram_mb)
    echo "Low-memory device detected (${ram_mb}MB RAM). Expanding swap for installation."

    local SWAP_CONF="/etc/dphys-swapfile"
    if [ -f "$SWAP_CONF" ]; then
      local CURRENT_SWAP
      CURRENT_SWAP=$(grep -E "^CONF_SWAPSIZE=" "$SWAP_CONF" | cut -d= -f2)
      if [ "${CURRENT_SWAP:-0}" -lt 2048 ]; then
        sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' "$SWAP_CONF"
        sudo systemctl restart dphys-swapfile
        echo_success "\tSwap expanded to 2GB"
      else
        echo_success "\tSwap already at ${CURRENT_SWAP}MB"
      fi
    fi
  fi
}

wait_for_apt() {
  # Wait for any running apt/dpkg processes to finish
  while sudo fuser /var/lib/dpkg/lock-frontend > /dev/null 2>&1; do
    sleep 1
  done
}

setup_zramswap_service() {
  echo "Enabling and starting zramswap service."
  wait_for_apt
  sudo apt-get install -y zram-tools > /dev/null 2>&1
  echo -e "ALGO=zstd\nPERCENT=60" | sudo tee /etc/default/zramswap > /dev/null
  sudo systemctl enable --now zramswap
}

setup_earlyoom_service() {
  echo "Enabling and starting earlyoom service."
  wait_for_apt
  sudo apt-get install -y earlyoom > /dev/null 2>&1
  sudo systemctl enable --now earlyoom
}

create_venv(){
  echo "Creating python virtual environment. "
  python3 -m venv "$VENV_PATH"
  $VENV_PATH/bin/python -m pip install --no-cache-dir --upgrade pip setuptools wheel > /dev/null

  if is_low_memory; then
    # On low-memory devices, install packages in small batches to avoid OOM.
    # Runs pip in foreground (not background) to properly detect failures.
    echo "Installing python dependencies (low-memory mode)..."
    local batch_size=5
    local batch=1
    local tmpfile
    tmpfile=$(mktemp)

    # Extract non-comment, non-empty lines
    grep -v '^\s*#\|^\s*$' "$PIP_REQUIREMENTS_FILE" > "$tmpfile"
    local total
    total=$(wc -l < "$tmpfile")
    local total_batches=$(( (total + batch_size - 1) / batch_size ))

    local batch_file
    batch_file=$(mktemp)
    local line_num=0

    while IFS= read -r line; do
      echo "$line" >> "$batch_file"
      line_num=$((line_num + 1))

      if [ $((line_num % batch_size)) -eq 0 ] || [ "$line_num" -eq "$total" ]; then
        printf "\tInstalling python dependencies (batch $batch/$total_batches)... "
        if $VENV_PATH/bin/python -m pip install --no-cache-dir -r "$batch_file" -qq > /dev/null 2>&1; then
          printf "[\e[32m\xE2\x9C\x94\e[0m]\n"
        else
          printf "[\e[31m\xE2\x9C\x98\e[0m]\n"
          echo_error "\tBatch $batch failed. Retrying with output..."
          $VENV_PATH/bin/python -m pip install --no-cache-dir -r "$batch_file" 2>&1 | tail -10
        fi
        batch=$((batch + 1))
        : > "$batch_file"
      fi
    done < "$tmpfile"

    rm -f "$tmpfile" "$batch_file"
  else
    $VENV_PATH/bin/python -m pip install --no-cache-dir -r $PIP_REQUIREMENTS_FILE -qq > /dev/null &
    show_loader "\tInstalling python dependencies. "
  fi

  # Install Waveshare e-paper GPIO dependencies if ws-requirements.txt exists
  WS_REQUIREMENTS="$SCRIPT_DIR/ws-requirements.txt"
  if [ -f "$WS_REQUIREMENTS" ]; then
    $VENV_PATH/bin/python -m pip install --no-cache-dir -r $WS_REQUIREMENTS -qq > /dev/null 2>&1 &
    show_loader "\tInstalling e-paper display dependencies. "
  fi
}

install_app_service() {
  echo "Installing $APPNAME systemd service."
  # Generate service file from template with correct APPNAME
  cat > "$SERVICE_FILE_TARGET" <<EOF
[Unit]
Description=$APPNAME App
After=${APPNAME}-splash.service
Wants=network-online.target

[Service]
User=root
RuntimeDirectory=$APPNAME
WorkingDirectory=/run/$APPNAME
ExecStart=/usr/local/bin/$APPNAME run
Restart=on-failure
RestartSec=60
KillSignal=SIGINT
StandardOutput=journal
StandardError=journal
CPUQuota=40%
MemoryMax=380M

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable $SERVICE_FILE
}

setup_clean_boot() {
  echo "Configuring clean boot experience."

  # 1. Update kernel cmdline for quiet boot
  CMDLINE_FILE="/boot/firmware/cmdline.txt"
  if [ ! -f "$CMDLINE_FILE" ]; then
    CMDLINE_FILE="/boot/cmdline.txt"
  fi

  if [ -f "$CMDLINE_FILE" ]; then
    CMDLINE=$(cat "$CMDLINE_FILE" | tr -d '\n')
    PARAMS_TO_ADD="quiet splash loglevel=0 logo.nologo vt.global_cursor_default=0 consoleblank=1"
    MODIFIED=false
    for param in $PARAMS_TO_ADD; do
      if ! echo "$CMDLINE" | grep -q "$param"; then
        CMDLINE="$CMDLINE $param"
        MODIFIED=true
      fi
    done
    if [ "$MODIFIED" = true ]; then
      cp "$CMDLINE_FILE" "${CMDLINE_FILE}.dashpi.bak"
      echo "$CMDLINE" > "$CMDLINE_FILE"
      echo_success "\tUpdated $CMDLINE_FILE with quiet boot parameters"
    else
      echo_success "\tKernel cmdline already configured"
    fi
  fi

  # 2. Suppress GPU rainbow splash via config.txt
  CONFIG_TXT="/boot/firmware/config.txt"
  if [ ! -f "$CONFIG_TXT" ]; then
    CONFIG_TXT="/boot/config.txt"
  fi
  if [ -f "$CONFIG_TXT" ]; then
    if ! grep -q "disable_splash=1" "$CONFIG_TXT"; then
      echo "disable_splash=1" >> "$CONFIG_TXT"
      echo_success "\tDisabled GPU rainbow splash in config.txt"
    fi
  fi

  # 3. Mask getty on tty1 (SSH unaffected, tty2+ still available)
  systemctl mask getty@tty1.service > /dev/null 2>&1
  echo_success "\tMasked getty@tty1.service"

  # 4. Install tmpfiles config for fbcon cursor
  if [ -f "$SCRIPT_DIR/dashpi-fbcon.conf" ]; then
    cp "$SCRIPT_DIR/dashpi-fbcon.conf" /etc/tmpfiles.d/
    echo_success "\tInstalled fbcon cursor config"
  fi

  # 5. Install splash animation script (LCD only)
  if [ -f "$SCRIPT_DIR/show_splash.py" ]; then
    cp "$SCRIPT_DIR/show_splash.py" "$INSTALL_PATH/show_splash.py"
    echo_success "\tInstalled splash animation script"
  fi

  # 6. Install and enable splash service
  if [ -f "$SCRIPT_DIR/dashpi-splash.service" ]; then
    sed "s/dashpi/${APPNAME}/g" "$SCRIPT_DIR/dashpi-splash.service" > "/etc/systemd/system/${APPNAME}-splash.service"
    systemctl daemon-reload
    systemctl enable "${APPNAME}-splash.service" > /dev/null 2>&1
    echo_success "\tInstalled and enabled ${APPNAME}-splash.service"
  fi
}

install_executable() {
  echo "Adding executable to ${BINPATH}/$APPNAME"
  cp $SCRIPT_DIR/dashpi $BINPATH/
  sudo chmod +x $BINPATH/$APPNAME
}

install_config() {
  CONFIG_BASE_DIR="$SCRIPT_DIR/config_base"
  CONFIG_DIR="$SRC_PATH/config"
  echo "Copying config files to $CONFIG_DIR"

  # Check and copy device.config if it doesn't exist
  if [ ! -f "$CONFIG_DIR/device.json" ]; then
    cp "$CONFIG_BASE_DIR/device.json" "$CONFIG_DIR/"
    show_loader "\tCopying device.config to $CONFIG_DIR"
  else
    echo_success "\tdevice.json already exists in $CONFIG_DIR"
  fi
}

stop_service() {
    echo "Checking if $SERVICE_FILE is running"
    if /usr/bin/systemctl is-active --quiet $SERVICE_FILE
    then
      /usr/bin/systemctl stop $SERVICE_FILE > /dev/null &
      show_loader "Stopping $APPNAME service"
    else
      echo_success "\t$SERVICE_FILE not running"
    fi
}

start_service() {
  echo "Starting $APPNAME service."
  sudo systemctl start $SERVICE_FILE
}

install_src() {
  # Check if an existing installation is present
  echo "Installing $APPNAME to $INSTALL_PATH"
  if [[ -d $INSTALL_PATH ]]; then
    rm -rf "$INSTALL_PATH" > /dev/null
    show_loader "\tRemoving existing installation found at $INSTALL_PATH"
  fi

  mkdir -p "$INSTALL_PATH"

  ln -sf "$SRC_PATH" "$INSTALL_PATH/src"
  show_loader "\tCreating symlink from $SRC_PATH to $INSTALL_PATH/src"
}

install_cli() {
  cp -r "$SCRIPT_DIR/cli" "$INSTALL_PATH/"
  sudo chmod +x "$INSTALL_PATH/cli/"*
}

# Get Raspberry Pi hostname
get_hostname() {
  echo "$(hostname)"
}

# Get Raspberry Pi IP address
get_ip_address() {
  ip_address=$(hostname -I | awk '{print $1}')
  echo "$ip_address"
}

# Get OS release number, e.g. 11=Bullseye, 12=Bookworm, 13=Trixe
get_os_version() {
  echo "$(lsb_release -sr)"
}

ask_for_reboot() {
  # Get hostname and IP address
  hostname=$(get_hostname)
  ip_address=$(get_ip_address)
  echo_header "$(echo_success "${APPNAME^^} Installation Complete!")"
  echo_header "[•] A reboot of your Raspberry Pi is required for the changes to take effect"
  echo_header "[•] After your Pi is rebooted, you can access the web UI by going to $(echo_blue "'$hostname.local'") or $(echo_blue "'$ip_address'") in your browser."

  read -p "Would you like to restart your Raspberry Pi now? [Y/N] " userInput
  userInput="${userInput^^}"

  if [[ "${userInput,,}" == "y" ]]; then
    echo_success "You entered 'Y', rebooting now..."
    sleep 2
    sudo reboot now
  elif [[ "${userInput,,}" == "n" ]]; then
    echo "Please restart your Raspberry Pi later to apply changes by running 'sudo reboot now'."
    exit
  else
    echo "Unknown input, please restart your Raspberry Pi later to apply changes by running 'sudo reboot now'."
    sleep 1
  fi
}

enable_interfaces() {
  echo "Enabling hardware interfaces."
  # Enable I2C (required for Inky e-paper auto-detection)
  if sudo raspi-config nonint get_i2c | grep -q "1"; then
    sudo raspi-config nonint do_i2c 0
    echo_success "\tI2C enabled"
  else
    echo_success "\tI2C already enabled"
  fi
  # Enable SPI (required for e-paper and some display communication)
  if sudo raspi-config nonint get_spi | grep -q "1"; then
    sudo raspi-config nonint do_spi 0
    echo_success "\tSPI enabled"
  else
    echo_success "\tSPI already enabled"
  fi

  # Add spi0-0cs overlay to prevent kernel from claiming the SPI chip select pin.
  # Required for Inky e-paper displays on newer kernels (6.x+).
  CONFIG_TXT="/boot/firmware/config.txt"
  if [ ! -f "$CONFIG_TXT" ]; then
    CONFIG_TXT="/boot/config.txt"
  fi
  if [ -f "$CONFIG_TXT" ]; then
    if ! grep -q "spi0-0cs" "$CONFIG_TXT"; then
      echo "dtoverlay=spi0-0cs" >> "$CONFIG_TXT"
      echo_success "\tAdded spi0-0cs overlay for e-paper compatibility"
    else
      echo_success "\tspi0-0cs overlay already configured"
    fi
  fi
}

check_permissions
stop_service
enable_interfaces
install_debian_dependencies
# check OS version for Bookworm to setup zramswap
if [[ $(get_os_version) = "12" ]] ; then
  echo "OS version is Bookworm - setting up zramswap"
  setup_zramswap_service
else
  echo "OS version is not Bookworm - skipping zramswap setup."
fi
setup_earlyoom_service
setup_swap
install_src
install_cli
create_venv
install_executable
install_config
install_app_service
setup_clean_boot

echo "Update JS and CSS files"
bash $SCRIPT_DIR/update_vendors.sh > /dev/null

ask_for_reboot
