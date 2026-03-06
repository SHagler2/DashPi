#!/bin/bash

# =============================================================================
# DashPi Bootstrap Script
# One-liner install: curl -sSL https://raw.githubusercontent.com/SHagler2/DashPi/main/install/bootstrap.sh | sudo bash
#
# Installs git if needed, clones the repo, and runs the full install script.
# =============================================================================

set -e

REPO_URL="https://github.com/SHagler2/DashPi.git"
INSTALL_DIR="$HOME/DashPi"

# When run via sudo, $HOME is /root — install to the calling user's home instead
if [ -n "$SUDO_USER" ]; then
    INSTALL_DIR=$(eval echo "~$SUDO_USER")/DashPi
fi

echo "=== DashPi Bootstrap ==="

# Install git if not available
if ! command -v git &> /dev/null; then
    echo "Installing git..."
    apt-get update -qq
    apt-get install -y -qq git
    echo "git installed."
fi

# Clone or update repo
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "DashPi repo already exists at $INSTALL_DIR, pulling latest..."
    git -C "$INSTALL_DIR" pull
else
    echo "Cloning DashPi to $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# Fix ownership if cloned as root via sudo
if [ -n "$SUDO_USER" ]; then
    chown -R "$SUDO_USER:$(id -gn "$SUDO_USER")" "$INSTALL_DIR"
fi

echo "Running install script..."
exec bash "$INSTALL_DIR/install/install.sh"
