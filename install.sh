#!/bin/bash

# MeshVM Daemon Installation Script
# Run as root or with sudo

set -e

echo "Installing MeshVM daemon..."

# Create system user
if ! id "meshvm" &>/dev/null; then
    useradd -r -s /bin/false -M -d /var/lib/meshvm meshvm
    echo "Created meshvm user"
fi

# Create directories
mkdir -p /etc/meshvm
mkdir -p /var/log
mkdir -p /var/lib/meshvm
mkdir -p /var/run

# Set permissions
chown meshvm:meshvm /var/lib/meshvm
chown meshvm:meshvm /var/log

# Install Python dependencies
pip3 install pyserial paho-mqtt meshtastic

# Copy configuration file if it doesn't exist
if [ ! -f /etc/meshvm/meshvm.conf ]; then
    cp meshvm.conf.example /etc/meshvm/meshvm.conf
    echo "Configuration file copied to /etc/meshvm/meshvm.conf"
    echo "Please edit this file and set your node_id before starting the daemon"
fi

# Copy daemon script
cp meshvm.py /usr/local/bin/meshvm
chmod +x /usr/local/bin/meshvm

echo "Installation complete!"
echo ""
echo "Next steps:"
echo "1. Edit /etc/meshvm/meshvm.conf and set your node_id"
echo "2. Configure your MQTT broker settings"
echo "3. Add your keywords and corresponding MQTT topics"
echo "4. Test: /usr/local/bin/meshvm --foreground"
echo "5. Install systemd service: sudo cp meshvm.service /etc/systemd/system/"
echo "6. Enable and start: sudo systemctl enable meshvm && sudo systemctl start meshvm"