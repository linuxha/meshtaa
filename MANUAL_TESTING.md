# Manual Testing Guide for MeshVM Daemon

## Prerequisites

### 1. Install Python Dependencies
```bash
# Install required Python packages
pip3 install pyserial paho-mqtt meshtastic

# Or use the requirements file if you have it
pip3 install -r requirements.txt
```

### 2. Find Your Meshtastic Device
```bash
# List serial devices to find your Meshtastic device
ls -la /dev/tty*

# Common locations:
# /dev/ttyUSB0  - USB-to-Serial adapter
# /dev/ttyACM0  - Direct USB connection
# /dev/ttyUSB1, /dev/ttyUSB2, etc. - Multiple devices

# Check device permissions
ls -la /dev/ttyUSB0

# Add your user to dialout group if needed
sudo usermod -a -G dialout $USER
# (logout and login again after this)
```

### 3. Get Your Node ID
```bash
# Connect to your Meshtastic device and get info
meshtastic --port /dev/ttyUSB0 --info

# Look for output like:
# My info: {'num': 123456789, 'user': {'id': '!1e240d15', ...}}
# The 'num' field is your node_id
```

## Manual Setup and Testing

### 1. Create a Test Configuration
```bash
# Create a local config directory
mkdir -p ~/meshvm-test

# Create configuration file
cat > ~/meshvm-test/meshvm.conf << 'EOF'
[meshtastic]
serial_port = /dev/ttyUSB0
baudrate = 115200
node_id = YOUR_NODE_ID_HERE

[mqtt]
broker = localhost
port = 1883
username = 
password = 
keepalive = 60

[daemon]
log_file = ~/meshvm-test/meshvm.log
log_level = DEBUG
pid_file = ~/meshvm-test/meshvm.pid
history_file = ~/meshvm-test/history.md

[keywords]
weather = sensors/weather
status = system/status
temp = sensors/temperature
ping = system/ping
test = test/topic
EOF

echo "Edit ~/meshvm-test/meshvm.conf and replace YOUR_NODE_ID_HERE with your actual node ID"
```

### 2. Set Up Test MQTT Broker (Optional)
```bash
# Install mosquitto MQTT broker (Ubuntu/Debian)
sudo apt install mosquitto mosquitto-clients

# Start mosquitto
sudo systemctl start mosquitto

# Test MQTT connection (verify broker is reachable)
mosquitto_sub -h localhost -t "test/topic" -v --timeout 2
```

**Note**: In production, external applications should publish data to MQTT topics using the retain flag (`-r`) so MeshVM can access the latest values even after connecting to the broker.

### 3. Test the Daemon

#### First, make the script executable:
```bash
chmod +x meshvm.py
```

#### Check version and help:
```bash
# Check version
./meshvm.py --version

# Show help
./meshvm.py --help
```

#### Generate a sample config (alternative method):
```bash
# Generate sample config
./meshvm.py --create-config --config ~/meshvm-test/meshvm.conf

# Edit the generated config
nano ~/meshvm-test/meshvm.conf
```

#### Run in foreground for testing:
```bash
# Run with your test config
./meshvm.py --config /home/njc/meshvm-test/meshvm.conf --foreground

# You should see output like:
# 2026-02-05 14:30:15,123 - MeshVM - INFO - Starting MeshVM daemon v0.1.0
# 2026-02-05 14:30:15,124 - MeshVM - INFO - Logging initialized
# 2026-02-05 14:30:15,125 - MeshVM - INFO - Connecting to MQTT broker localhost:1883
# 2026-02-05 14:30:15,126 - MeshVM - INFO - Connected to MQTT broker
# 2026-02-05 14:30:15,127 - MeshVM - INFO - Connecting to Meshtastic device on /dev/ttyUSB0
# 2026-02-05 14:30:16,200 - MeshVM - INFO - Connected to Meshtastic node ID: 123456789
# 2026-02-05 14:30:16,201 - MeshVM - INFO - Chat history logging to: ~/meshvm-test/history.md
# 2026-02-05 14:30:16,202 - MeshVM - INFO - Starting message monitoring
```

## Testing Message Processing

### 1. Send Test Messages
Using another Meshtastic device or the CLI, send messages to your node:

```bash
# From another device, send messages containing keywords:
meshtastic --port /dev/ttyUSB1 --dest YOUR_NODE_ID --sendtext "What's the weather?"
meshtastic --port /dev/ttyUSB1 --dest YOUR_NODE_ID --sendtext "System status please"
meshtastic --port /dev/ttyUSB1 --dest YOUR_NODE_ID --sendtext "Hello test"
```

### 2. Monitor Logs and History
```bash
# In another terminal, watch the logs
tail -f ~/meshvm-test/meshvm.log

# Watch the chat history
tail -f ~/meshvm-test/history.md
```

## Expected Behavior

### Successful Startup Checklist:
- ✅ Python dependencies installed
- ✅ Serial device accessible
- ✅ MQTT broker reachable
- ✅ Node ID configured correctly
- ✅ Chat history file created
- ✅ Message monitoring started

### Message Processing:
1. **Incoming Message**: "Hey, what's the weather like?"
2. **Keyword Detection**: "weather" found
3. **MQTT Lookup**: Gets data from "sensors/weather" topic (if available from existing MQTT sources published by external applications with retain flag)
4. **Response Sent**: "Weather: [data from MQTT topic]" or "Weather: No data available"
5. **History Logged**: Interaction saved to history.md

**Note**: For reliable data availability, external applications should periodically publish retained data (`-r` flag) to the MQTT topics configured in your keywords section.

### Log Files:
```bash
# View daemon logs
cat ~/meshvm-test/meshvm.log

# View chat history
cat ~/meshvm-test/history.md

# Check PID file
cat ~/meshvm-test/meshvm.pid
```

## Troubleshooting

### Common Issues:

1. **Permission Denied on Serial Port**
```bash
sudo chmod 666 /dev/ttyUSB0
# Or add user to dialout group (better solution)
sudo usermod -a -G dialout $USER
```

2. **MQTT Connection Failed**
```bash
# Test MQTT connectivity (subscribe test)
mosquitto_sub -h localhost -t test -v --timeout 5
# Check if mosquitto is running
sudo systemctl status mosquitto
```

3. **Node ID Issues**
```bash
# Get node info again
meshtastic --port /dev/ttyUSB0 --info
# Make sure the node_id in config matches the 'num' field
```

4. **No Messages Received**
- Check that messages are addressed to your specific node ID
- Verify Meshtastic device is properly connected
- Check serial port path in config

### Debug Mode:
```bash
# Run with debug logging
# Edit config: log_level = DEBUG
./meshvm.py --config ~/meshvm-test/meshvm.conf --foreground
```

## Stopping the Daemon
```bash
# If running in foreground, use Ctrl+C
# The daemon will:
# 1. Disconnect from Meshtastic device
# 2. Disconnect from MQTT broker
# 3. Remove PID file
# 4. Log shutdown message
```

## File Structure After Testing
```
~/meshvm-test/
├── meshvm.conf      # Your configuration
├── meshvm.log       # Daemon logs
├── meshvm.pid       # Process ID (when running)
└── history.md       # Chat interaction history
```

This manual testing approach lets you verify all functionality before installing as a system service.