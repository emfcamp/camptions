#!/bin/bash
#
# EMF Camptions - Raspberry Pi Display Setup
#
# This script sets up a Raspberry Pi to display captions on a connected
# screen in kiosk mode using Chromium.
#
# Usage: sudo ./setup-display.sh
#

set -e

# Configuration
CAMPTIONS_URL="${CAMPTIONS_URL:-https://stages.emf.camp/display}"
CAMPTIONS_VENUE="${CAMPTIONS_VENUE:-stage-a}"
DISPLAY_ROTATION="${DISPLAY_ROTATION:-normal}"  # normal, left, right, inverted

echo "========================================"
echo "EMF Camptions - Display Setup"
echo "========================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo $0"
    exit 1
fi

# Resolve the user the kiosk should run as — the one who invoked sudo.
# This is destructive: it overwrites this user's openbox autostart and turns on
# autologin. Use a Pi dedicated to caption display.
DISPLAY_USER="${SUDO_USER:-$USER}"
if [ "$DISPLAY_USER" = "root" ] || ! id "$DISPLAY_USER" &>/dev/null; then
    echo "Error: Could not determine a non-root user."
    echo "Run this script via sudo from a regular user account, e.g.:"
    echo "  sudo ./setup-display.sh"
    exit 1
fi
echo "Configuring kiosk for user: $DISPLAY_USER"
echo "(This will enable autologin and replace the user's openbox autostart.)"
echo ""

echo "[1/9] Updating system packages..."
apt-get update
apt-get upgrade -y

echo "[2/9] Installing display dependencies..."
apt-get install -y \
    chromium-browser \
    xserver-xorg \
    x11-xserver-utils \
    xinit \
    openbox \
    unclutter \
    lightdm

echo "[3/9] Granting display/input groups to $DISPLAY_USER..."
usermod -a -G video,audio,input,tty "$DISPLAY_USER"

echo "[4/9] Configuring autologin..."
mkdir -p /etc/lightdm/lightdm.conf.d

cat > /etc/lightdm/lightdm.conf.d/autologin.conf << EOF
[Seat:*]
autologin-user=$DISPLAY_USER
autologin-user-timeout=0
user-session=openbox
EOF

echo "[5/9] Configuring display rotation..."
# Set rotation in xorg config
if [ "$DISPLAY_ROTATION" != "normal" ]; then
    mkdir -p /etc/X11/xorg.conf.d

    case "$DISPLAY_ROTATION" in
        left)
            ROTATION_OPTION="left"
            ;;
        right)
            ROTATION_OPTION="right"
            ;;
        inverted)
            ROTATION_OPTION="inverted"
            ;;
        *)
            ROTATION_OPTION="normal"
            ;;
    esac

    cat > /etc/X11/xorg.conf.d/10-monitor.conf << EOF
Section "Monitor"
    Identifier "HDMI-1"
    Option "Rotate" "$ROTATION_OPTION"
EndSection
EOF
fi

echo "[6/9] Creating kiosk startup script..."
mkdir -p "/home/$DISPLAY_USER/.config/openbox"

cat > "/home/$DISPLAY_USER/.config/openbox/autostart" << AUTOSTART_EOF
#!/bin/bash

# Load configuration
source /home/$DISPLAY_USER/camptions-display.conf 2>/dev/null || true

# Disable screen blanking and power management
xset s off
xset s noblank
xset -dpms

# Hide mouse cursor after 0.5 seconds of inactivity
unclutter -idle 0.5 -root &

# Wait for network
sleep 5

# Build the caption display URL
CAPTION_URL="\${CAMPTIONS_URL:-https://stages.emf.camp/display}?venue=\${CAMPTIONS_VENUE:-stage-a}&mode=\${DISPLAY_MODE:-dark}"

if [ -n "\$FONT_SIZE" ]; then
    CAPTION_URL="\${CAPTION_URL}&fontSize=\${FONT_SIZE}"
fi

if [ -n "\$MAX_LINES" ]; then
    CAPTION_URL="\${CAPTION_URL}&maxLines=\${MAX_LINES}"
fi

# Start Chromium in kiosk mode
chromium-browser \\
    --kiosk \\
    --noerrdialogs \\
    --disable-infobars \\
    --disable-session-crashed-bubble \\
    --disable-restore-session-state \\
    --no-first-run \\
    --start-fullscreen \\
    --autoplay-policy=no-user-gesture-required \\
    --disable-features=TranslateUI \\
    --check-for-update-interval=31536000 \\
    --disable-background-networking \\
    --disable-component-update \\
    --disable-default-apps \\
    --disable-extensions \\
    --disable-sync \\
    --incognito \\
    "\$CAPTION_URL" &
AUTOSTART_EOF

chown -R "$DISPLAY_USER:$DISPLAY_USER" "/home/$DISPLAY_USER/.config"
chmod +x "/home/$DISPLAY_USER/.config/openbox/autostart"

echo "[7/9] Creating configuration file..."
cat > "/home/$DISPLAY_USER/camptions-display.conf" << EOF
# EMF Camptions Display Configuration
# Edit this file and reboot to apply changes

# Caption server URL
CAMPTIONS_URL=$CAMPTIONS_URL

# Venue ID
CAMPTIONS_VENUE=$CAMPTIONS_VENUE

# Display rotation: normal, left, right, inverted
DISPLAY_ROTATION=$DISPLAY_ROTATION

# Display mode: dark, light, high-contrast
DISPLAY_MODE=dark

# Font size (CSS value, e.g., 4vw, 48px)
FONT_SIZE=4vw

# Maximum lines to display
MAX_LINES=8
EOF

chown "$DISPLAY_USER:$DISPLAY_USER" "/home/$DISPLAY_USER/camptions-display.conf"

echo "[8/9] Creating management scripts..."

# Script to reload display
cat > /usr/local/bin/camptions-display-reload << EOF
#!/bin/bash
# Reload the caption display by restarting Chromium
pkill -f chromium
sleep 2
sudo -u $DISPLAY_USER openbox --replace &
EOF
chmod +x /usr/local/bin/camptions-display-reload

# Script to show display status
cat > /usr/local/bin/camptions-display-status << EOF
#!/bin/bash
echo "Camptions Display Status"
echo "========================"
echo ""
echo "Chromium process:"
pgrep -a chromium || echo "  Not running"
echo ""
echo "Display:"
DISPLAY=:0 xrandr 2>/dev/null | head -5 || echo "  Cannot query display"
echo ""
echo "Configuration:"
cat /home/$DISPLAY_USER/camptions-display.conf
EOF
chmod +x /usr/local/bin/camptions-display-status

# Script to set venue
cat > /usr/local/bin/camptions-set-venue << EOF
#!/bin/bash
if [ -z "\$1" ]; then
    echo "Usage: camptions-set-venue <venue-id>"
    echo "Example: camptions-set-venue stage-b"
    exit 1
fi
sed -i "s/^CAMPTIONS_VENUE=.*/CAMPTIONS_VENUE=\$1/" /home/$DISPLAY_USER/camptions-display.conf
echo "Venue set to: \$1"
echo "Reloading display..."
camptions-display-reload
EOF
chmod +x /usr/local/bin/camptions-set-venue

echo "[9/9] Configuring boot options..."
# Disable splash screen for faster boot
if [ -f /boot/cmdline.txt ]; then
    if ! grep -q "consoleblank=0" /boot/cmdline.txt; then
        sed -i 's/$/ consoleblank=0/' /boot/cmdline.txt
    fi
fi

# Disable overscan (better display utilization)
if [ -f /boot/config.txt ]; then
    if ! grep -q "disable_overscan=1" /boot/config.txt; then
        echo "disable_overscan=1" >> /boot/config.txt
    fi
fi

echo ""
echo "========================================"
echo "Setup complete!"
echo "========================================"
echo ""
echo "Configuration file:"
echo "  /home/$DISPLAY_USER/camptions-display.conf"
echo ""
echo "Management commands:"
echo "  camptions-display-status  - Show current status"
echo "  camptions-display-reload  - Reload the display"
echo "  camptions-set-venue <id>  - Change venue and reload"
echo ""
echo "The display will start automatically on next boot."
echo ""
echo "To test now, reboot the Pi:"
echo "  sudo reboot"
echo ""
