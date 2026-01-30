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
CAMPTIONS_USER="camptions"
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

echo "[1/8] Updating system packages..."
apt-get update
apt-get upgrade -y

echo "[2/8] Installing dependencies..."
apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-pyaudio \
    portaudio19-dev \
    alsa-utils \
    git

echo "[3/8] Creating camptions user..."
if ! id "$CAMPTIONS_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$CAMPTIONS_USER"
fi

# Add user to audio group
usermod -a -G audio "$CAMPTIONS_USER"

echo "[4/8] Setting up application directory..."
mkdir -p "$CAMPTIONS_DIR"
chown "$CAMPTIONS_USER:$CAMPTIONS_USER" "$CAMPTIONS_DIR"

echo "[5/8] Creating Python virtual environment..."
sudo -u "$CAMPTIONS_USER" python3 -m venv "$CAMPTIONS_DIR/venv"
sudo -u "$CAMPTIONS_USER" "$CAMPTIONS_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$CAMPTIONS_USER" "$CAMPTIONS_DIR/venv/bin/pip" install \
    pyaudio \
    websockets

echo "[6/8] Installing capture client..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Copy capture_client.py if it exists in script directory
if [ -f "$SCRIPT_DIR/capture_client.py" ]; then
    cp "$SCRIPT_DIR/capture_client.py" "$CAMPTIONS_DIR/capture_client.py"
else
    echo "Warning: capture_client.py not found in $SCRIPT_DIR"
    echo "Please manually copy capture_client.py to $CAMPTIONS_DIR/"
fi

chown "$CAMPTIONS_USER:$CAMPTIONS_USER" "$CAMPTIONS_DIR/capture_client.py" 2>/dev/null || true
chmod +x "$CAMPTIONS_DIR/capture_client.py" 2>/dev/null || true

echo "[7/8] Creating systemd service..."
cat > /etc/systemd/system/camptions-capture.service << EOF
[Unit]
Description=EMF Camptions Audio Capture
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=$CAMPTIONS_USER
Group=$CAMPTIONS_USER
WorkingDirectory=$CAMPTIONS_DIR
EnvironmentFile=$CAMPTIONS_DIR/config.env
ExecStart=$CAMPTIONS_DIR/venv/bin/python3 $CAMPTIONS_DIR/capture_client.py \\
    --server "\${CAMPTIONS_SERVER}" \\
    --venue "\${CAMPTIONS_VENUE}"
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

echo "[8/8] Creating configuration file..."
cat > "$CAMPTIONS_DIR/config.env" << EOF
# EMF Camptions Audio Capture Configuration
# Edit this file and restart the service to apply changes

# Server URL (WebSocket)
CAMPTIONS_SERVER=$CAMPTIONS_SERVER

# Venue ID
CAMPTIONS_VENUE=$CAMPTIONS_VENUE

# Audio device index (leave empty for default)
# Run 'camptions-list-devices' to see available devices
CAMPTIONS_DEVICE=
EOF

chown "$CAMPTIONS_USER:$CAMPTIONS_USER" "$CAMPTIONS_DIR/config.env"

# Create helper script for listing audio devices
cat > /usr/local/bin/camptions-list-devices << EOF
#!/bin/bash
sudo -u $CAMPTIONS_USER $CAMPTIONS_DIR/venv/bin/python3 $CAMPTIONS_DIR/capture_client.py --list-devices
EOF
chmod +x /usr/local/bin/camptions-list-devices

# Create helper script for testing
cat > /usr/local/bin/camptions-test << EOF
#!/bin/bash
source $CAMPTIONS_DIR/config.env
sudo -u $CAMPTIONS_USER $CAMPTIONS_DIR/venv/bin/python3 $CAMPTIONS_DIR/capture_client.py \\
    --server "\$CAMPTIONS_SERVER" \\
    --venue "\$CAMPTIONS_VENUE" \\
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
