#!/bin/bash
#
# EMF Camptions - Raspberry Pi Audio Capture Setup
#
# This script sets up a Raspberry Pi to capture audio and stream
# it to the central camptions server via WebSocket.
#
# Usage: sudo ./setup-audio-capture.sh
#

set -e

# Configuration
CAMPTIONS_DIR="/opt/camptions"
CAMPTIONS_SERVER="${CAMPTIONS_SERVER:-ws://captions.emf.camp}"
CAMPTIONS_VENUE="${CAMPTIONS_VENUE:-stage-a}"

echo "========================================"
echo "EMF Camptions - Audio Capture Setup"
echo "========================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo $0"
    exit 1
fi

# Resolve the unprivileged user to run the capture as — the one who invoked sudo
RUN_USER="${SUDO_USER:-$USER}"
if [ "$RUN_USER" = "root" ] || ! id "$RUN_USER" &>/dev/null; then
    echo "Error: Could not determine a non-root user."
    echo "Run this script via sudo from a regular user account, e.g.:"
    echo "  sudo ./setup-audio-capture.sh"
    exit 1
fi
echo "Installing for user: $RUN_USER"
echo ""

echo "[1/5] Refreshing apt index..."
apt-get update

echo "[2/5] Installing dependencies..."
apt-get install -y --no-install-recommends \
    python3 \
    python3-websockets \
    alsa-utils

echo "[3/5] Granting audio group access to $RUN_USER..."
usermod -a -G audio "$RUN_USER"

echo "[4/5] Setting up application directory..."
mkdir -p "$CAMPTIONS_DIR"
chown "$RUN_USER:$RUN_USER" "$CAMPTIONS_DIR"

echo "[5/5] Installing capture client..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Copy capture_client.py if it exists in script directory
if [ -f "$SCRIPT_DIR/capture_client.py" ]; then
    cp "$SCRIPT_DIR/capture_client.py" "$CAMPTIONS_DIR/capture_client.py"
else
    echo "Warning: capture_client.py not found in $SCRIPT_DIR"
    echo "Please manually copy capture_client.py to $CAMPTIONS_DIR/"
fi

chown "$RUN_USER:$RUN_USER" "$CAMPTIONS_DIR/capture_client.py" 2>/dev/null || true
chmod +x "$CAMPTIONS_DIR/capture_client.py" 2>/dev/null || true

cat > /etc/systemd/system/camptions-capture.service << EOF
[Unit]
Description=EMF Camptions Audio Capture
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$CAMPTIONS_DIR
EnvironmentFile=$CAMPTIONS_DIR/config.env
ExecStart=/usr/bin/python3 $CAMPTIONS_DIR/capture_client.py \\
    --server "\${CAMPTIONS_SERVER}" \\
    --venue "\${CAMPTIONS_VENUE}" \\
    \${CAMPTIONS_TOKEN:+--token "\${CAMPTIONS_TOKEN}"}
Restart=always
RestartSec=10

# Hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$CAMPTIONS_DIR

[Install]
WantedBy=multi-user.target
EOF

cat > "$CAMPTIONS_DIR/config.env" << EOF
# EMF Camptions Audio Capture Configuration
# Edit this file and restart the service to apply changes

# Server URL (WebSocket)
CAMPTIONS_SERVER=$CAMPTIONS_SERVER

# Venue ID
CAMPTIONS_VENUE=$CAMPTIONS_VENUE

# Ingest authentication token — must match CAMPTIONS_INGEST_TOKEN on the server
CAMPTIONS_TOKEN=

# Audio device (leave empty for auto-detect)
# Run 'camptions-list-devices' to see available devices
CAMPTIONS_DEVICE=
EOF

chown "$RUN_USER:$RUN_USER" "$CAMPTIONS_DIR/config.env"

# Create helper script for listing audio devices
cat > /usr/local/bin/camptions-list-devices << EOF
#!/bin/bash
sudo -u $RUN_USER /usr/bin/python3 $CAMPTIONS_DIR/capture_client.py --list-devices
EOF
chmod +x /usr/local/bin/camptions-list-devices

# Create helper script for testing
cat > /usr/local/bin/camptions-test << EOF
#!/bin/bash
source $CAMPTIONS_DIR/config.env
sudo -u $RUN_USER /usr/bin/python3 $CAMPTIONS_DIR/capture_client.py \\
    --server "\$CAMPTIONS_SERVER" \\
    --venue "\$CAMPTIONS_VENUE" \\
    \${CAMPTIONS_TOKEN:+--token "\$CAMPTIONS_TOKEN"} \\
    \${CAMPTIONS_DEVICE:+--device "\$CAMPTIONS_DEVICE"}
EOF
chmod +x /usr/local/bin/camptions-test

# Reload systemd
systemctl daemon-reload

echo ""
echo "========================================"
echo "Setup complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo ""
echo "1. Connect your USB audio device"
echo ""
echo "2. List available audio devices:"
echo "   camptions-list-devices"
echo ""
echo "3. Edit configuration:"
echo "   sudo nano $CAMPTIONS_DIR/config.env"
echo ""
echo "4. Test the capture (Ctrl+C to stop):"
echo "   camptions-test"
echo ""
echo "5. Enable and start the service:"
echo "   sudo systemctl enable camptions-capture"
echo "   sudo systemctl start camptions-capture"
echo ""
echo "6. Check service status:"
echo "   sudo systemctl status camptions-capture"
echo "   sudo journalctl -u camptions-capture -f"
echo ""
