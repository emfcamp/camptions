#!/bin/bash
#
# EMF Camptions - Full Raspberry Pi Setup
#
# This script sets up a Raspberry Pi for BOTH audio capture AND display.
# Useful for a self-contained stage setup.
#
# Usage: sudo ./setup-full.sh
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================"
echo "EMF Camptions - Full Pi Setup"
echo "========================================"
echo ""
echo "This will set up both audio capture and display."
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo $0"
    exit 1
fi

# Get configuration
read -p "Enter venue ID (e.g., stage-a): " VENUE_ID
read -p "Enter server URL [https://stages.emf.camp]: " SERVER_URL
SERVER_URL="${SERVER_URL:-https://stages.emf.camp}"

export CAMPTIONS_VENUE="$VENUE_ID"
export CAMPTIONS_SERVER="${SERVER_URL/https:/wss:}"
export CAMPTIONS_SERVER="${CAMPTIONS_SERVER/http:/ws:}"
export CAMPTIONS_URL="$SERVER_URL/display"

echo ""
echo "Configuration:"
echo "  Venue: $CAMPTIONS_VENUE"
echo "  Server: $CAMPTIONS_SERVER"
echo "  Display URL: $CAMPTIONS_URL"
echo ""
read -p "Continue? [y/N] " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 1
fi

# Run both setup scripts
echo ""
echo "Setting up audio capture..."
echo "========================================"
"$SCRIPT_DIR/setup-audio-capture.sh"

echo ""
echo "Setting up display..."
echo "========================================"
"$SCRIPT_DIR/setup-display.sh"

echo ""
echo "========================================"
echo "Full setup complete!"
echo "========================================"
echo ""
echo "Both audio capture and display are configured."
echo ""
echo "On reboot:"
echo "  - Audio will be captured and sent to the server"
echo "  - Display will show captions from the server"
echo ""
echo "To start now:"
echo "  sudo reboot"
echo ""
