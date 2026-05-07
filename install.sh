#!/bin/bash

# MeshVM Daemon Installation Script
# Run as root or with sudo

set -e

echo "Installing MeshVM daemon..."

# Create system user
if ! id "meshtaa" &>/dev/null; then
    useradd -r -s /bin/false -M -d /var/lib/meshtaa meshtaa
    echo "Created meshtaa user"
fi

# Create directories
mkdir -p /etc/meshtaa
mkdir -p /var/log
mkdir -p /var/lib/meshtaa
mkdir -p /var/run

# Set permissions
chown meshtaa:meshtaa /var/lib/meshtaa
chown meshtaa:meshtaa /var/log

# Install Python dependencies
pip3 install pyserial paho-mqtt meshtastic

# Copy configuration file if it doesn't exist
if [ ! -f /etc/meshtaa/meshtaa.conf ]; then
    cp meshtaa.conf.example /etc/meshtaa/meshtaa.conf
    echo "Configuration file copied to /etc/meshtaa/meshtaa.conf"
    echo "Please edit this file and set your node_id before starting the daemon"
fi

# Copy daemon script
cp meshtaa.py /usr/local/bin/meshtaa
chmod +x /usr/local/bin/meshtaa

echo "Installation complete!"
echo ""
echo "Next steps:"
echo "1. Edit /etc/meshtaa/meshtaa.conf and set your node_id"
echo "2. Configure your MQTT broker settings"
echo "3. Add your keywords and corresponding MQTT topics"
echo "4. Test: /usr/local/bin/meshtaa --foreground"
echo "5. Install systemd service: sudo cp meshtaa.service /etc/systemd/system/"
echo "6. Enable and start: sudo systemctl enable meshtaa && sudo systemctl start meshtaa"