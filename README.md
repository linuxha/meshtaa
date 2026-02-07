# Meshtaa - Meshtastic Auto Answer Daemon

This is what happens when to 'vide code'. I'll let the AI fix the renaming of the code from meshvm to meshtaa later.

Meshtaa is a serial port monitor that watches for certain keywords to be sent by individual users to this Meshtastic node. It will not monitor a broadcast and respond. I've chosen to use local MQTT topics to hold the keyword responses. This allows me to use cron or manual topic updates to provide information.

# "Vibe Coding"

Initially I named this application meshvm (meshtastic voice mail). And then let Copilot and Claude go and write Python code to talk to a serially attached Meshtastic node (RAK4631). Claude renamed it Meshtastic Virtual Machine Daemon, which is very wrong. This was an ill omen of things to come! ;-)

I have a paid, Github Copilot Pro account and I'm using the Claude AI.

Things started off okay but when I went to test the initial code it would work only if it was in VS Code with Claude running the code. Once I manually ran the code it was fail to reply. After a lot of back and forth, various patches with Claude actually getting frustrate (it said so), I went into debug mode and finally found the last bug, the reply message was too long (221 characters). We fixed that and here's what we have.

I'd like to note that when Claude is working and making sense it seems to work well. But when it gets off track it starts going Jr. Programmer and using the shotgun fix-it/diagnostics approach. Not exactly halucinations but not good programming practices (I've not taken advantage of the AI md files here). Letting the AI do it's think without feedback and control is a bad idea. Fortunately I'm running as a regular user so it couldn't do much real damage except to the code base. I still need to look over the code better but it appears to be technically correct and working now.

And for those who are wondering, yes I could have written this code without the AI but I'm trying to learn what are the limits and strengths of the AI. I've seen the AI do wonders and in a short time. I've also seen it get frustrated and fallback to the shotgun approach of diagnostics. Not what I expected. I still have a lot to learn but this has been interesting.

Now the rest of this file is pretty much AI written. I need to go through it but I have used half of it so it is technically sound.

## Features (my requirements)

- **Serial Port Monitoring**: Monitors Meshtastic device via serial connection
- **Message Filtering**: Only processes messages directed to your specific node ID
- **Keyword Processing**: Responds to configurable keywords with MQTT topic data
- **MQTT Integration**: Caches data from MQTT topics for quick responses
- **Daemon Operation**: Runs as a proper Linux daemon with systemd integration
- **Comprehensive Logging**: Detailed logging with configurable levels
- **Configuration Management**: Flexible configuration file support

## Architecture

```
┌─────────────────┐    Serial     ┌─────────────────┐
│   Meshtastic    │◄──────────────┤    MeshVM       │
│     Device      │               │    Daemon       │
└─────────────────┘               └─────────────────┘
                                          │
                                          │ MQTT
                                          ▼
                                  ┌─────────────────┐
                                  │   MQTT Broker   │
                                  │  (Topics/Data)  │
                                  └─────────────────┘
```

## Installation

### Prerequisites

- Python 3.7+
- Linux system with systemd
- Meshtastic device connected via USB/Serial
- MQTT broker (local or remote)

### Quick Install (not tested)

1. Clone or download the MeshVM files
2. Run the installation script as root:
   ```bash
   sudo ./install.sh
   ```

### Manual Installation

1. Install Python dependencies:
   ```bash
   pip3 install -r requirements.txt
   ```

2. Create system user:
   ```bash
   sudo useradd -r -s /bin/false -M meshvm
   ```

3. Create directories and copy files:
   ```bash
   sudo mkdir -p /etc/meshvm /var/lib/meshvm
   sudo cp meshvm.conf.example /etc/meshvm/meshvm.conf
   sudo cp meshvm.py /usr/local/bin/meshvm
   sudo chmod +x /usr/local/bin/meshvm
   ```

4. Install systemd service:
   ```bash
   sudo cp meshvm.service /etc/systemd/system/
   sudo systemctl daemon-reload
   ```

## Configuration

### 1. Find Your Node ID

First, determine your Meshtastic node ID:
```bash
meshtastic --info
```

Look for the "My info" section and note the node number.

### 2. Edit Configuration

Edit `/etc/meshvm/meshvm.conf`:

```ini
[meshtastic]
serial_port = /dev/ttyUSB0
node_id = CE:6E:13:A3:20:93  # MAC address format (or use !13a32093 or 329457811)

[mqtt]
broker = localhost
port = 1883
username = your_mqtt_user
password = your_mqtt_password

[keywords]
weather = sensors/outdoor/weather
status = system/status
temp = sensors/temperature/current
battery = power/battery/level
```

### 3. Set Up MQTT Topics

Ensure your MQTT broker has the topics you've configured with relevant data. External applications should periodically publish data to these topics with the retain flag (-r) so MeshVM can access the latest data:

```bash
# External applications should publish retained data like this:
mosquitto_pub -h localhost -t "sensors/weather" -r -m "Sunny, 22°C"
mosquitto_pub -h localhost -t "system/status" -r -m "All systems operational"
mosquitto_pub -h localhost -t "sensors/temperature" -r -m "Indoor: 21.5°C"
```

**Important**: Use the `-r` (retain) flag when publishing MQTT data so the latest values are available to MeshVM even if it connects after the data was published.

## Usage

### Testing

Run in foreground mode for testing:
```bash
sudo /usr/local/bin/meshvm --foreground
```

### Production

Enable and start the daemon:
```bash
sudo systemctl enable meshvm
sudo systemctl start meshvm
```

Check status:
```bash
sudo systemctl status meshvm
```

View logs:
```bash
sudo journalctl -u meshvm -f
```

### Creating Configuration

Generate a sample configuration file:
```bash
/usr/local/bin/meshvm --create-config --config /path/to/config.conf
```

## How It Works

1. **Startup**: Daemon connects to Meshtastic device via serial and MQTT broker
2. **MQTT Monitoring**: Subscribes to all configured MQTT topics and caches data
3. **Message Filtering**: Monitors all Meshtastic messages but only processes those directed to your node ID
4. **Keyword Detection**: Scans incoming messages for configured keywords
5. **Response**: When a keyword is found, retrieves cached MQTT data and sends response

### Example Interaction

A user can send a message directly to the Meshtastic node. A broadcast message will not work.

Appears to not be case sensitive.

```
User sends: "Hey, what's the weather?"
MeshVM sees: "weather" keyword
MeshVM responds: "Weather: Sunny, 22°C"
```

### Message Flow

```
Incoming Message → Filter by Node ID → Scan for Keywords → 
Lookup MQTT Data → Send Response
```

## Configuration Options

### Meshtastic Section
- `serial_port`: Device path (usually /dev/ttyUSB0)
- `baudrate`: Serial baud rate (default: 115200)
- `node_id`: Your Meshtastic node ID (supports multiple formats):
  - **Decimal format**: `123456789`
  - **Hex format**: `!146b40f5`
  - **MAC address format**: `CE:6E:13:A3:20:93` (uses last 4 octets → `!13a32093`)

### MQTT Section
- `broker`: MQTT broker hostname/IP
- `port`: MQTT broker port (default: 1883)
- `username`/`password`: Authentication (optional)
- `keepalive`: Connection keepalive seconds

### Keywords Section
- Format: `keyword = mqtt/topic/path`
- Keywords are case-insensitive
- First matching keyword wins

### Daemon Section
- `log_file`: Log file path
- `log_level`: DEBUG, INFO, WARNING, ERROR, CRITICAL
- `pid_file`: PID file location

## MQTT Data Management

MeshVM subscribes to MQTT topics and caches received data for keyword responses. For best results:

1. **Use Retained Messages**: Publish MQTT data with the retain flag (`-r`) so the latest values persist on the broker
2. **External Applications**: Set up external applications (sensors, scripts, monitoring tools) to periodically update MQTT topics
3. **Topic Structure**: Organize topics logically (e.g., `sensors/weather`, `system/status`, `devices/battery`)
4. **Data Freshness**: MeshVM caches MQTT data for 5 minutes by default - ensure your publishing frequency matches your needs

### Example External Data Sources

Be careful with the message size. Meshtastic has a limit on the number of characters you can send. It is dependent on the radio presets. For long/fast this is less that 200 characters.

```bash
# Weather monitoring script (cron every 10 minutes)
#!/bin/bash
WEATHER=$(curl -s "http://api.weather.com/current")
mosquitto_pub -h localhost -t "sensors/weather" -r -m "$WEATHER"

# System monitoring (cron every 5 minutes)
STATUS="Load: $(uptime | cut -d: -f4), Mem: $(free -m | awk 'NR==2{printf "%.1f%%", $3*100/$2}')%"
mosquitto_pub -h localhost -t "system/status" -r -m "$STATUS"

# Temperature sensor reading
TEMP=$(sensors | grep 'Package id 0' | awk '{print $4}')
mosquitto_pub -h localhost -t "sensors/temperature" -r -m "CPU: $TEMP"
```

## Troubleshooting

### Common Issues

1. **Permission Denied on Serial Port**
   ```bash
   sudo usermod -a -G dialout meshvm
   ```

2. **Node ID Not Found**
   - Check Meshtastic connection: `meshtastic --info`
   - Verify serial port in config
   - Ensure device is powered on

3. **MQTT Connection Failed**
   - Verify broker is running: `mosquitto_pub -h localhost -t test -m "hello"`
   - Check firewall settings
   - Verify credentials

4. **No Responses to Keywords**
   - Check MQTT topics have data
   - Verify keyword spelling in config
   - Check message is directed to your node ID

### Debug Mode

Run with debug logging:
```bash
# Edit config: log_level = DEBUG
sudo systemctl restart meshvm
sudo journalctl -u meshvm -f
```

### Log Analysis

```bash
# View recent logs
sudo tail -f /var/log/meshvm.log

# Search for specific issues
grep "ERROR" /var/log/meshvm.log
grep "keyword" /var/log/meshvm.log
```

## Security Considerations

- Daemon runs as unprivileged `meshvm` user
- Minimal file system access
- No network binding (only outgoing connections)
- Systemd security hardening enabled

## Development

### Testing Changes

1. Stop daemon: `sudo systemctl stop meshvm`
2. Run in foreground: `sudo -u meshvm /usr/local/bin/meshvm --foreground`
3. Send test messages to your Meshtastic node

### Adding Features

The codebase is modular:
- `MeshVMConfig`: Configuration management
- `MQTTManager`: MQTT client and caching
- `MeshtasticMonitor`: Serial monitoring and message processing
- `MeshVMDaemon`: Main daemon orchestration

## License

GPL 3.0 - Open source - modify and distribute as needed.

I'm a bit uncertain as to which Open Source License to use. At the moment I've chosen GPL 3.0.

## Support

For issues and feature requests, check the logs first, then verify your configuration matches your actual Meshtastic and MQTT setup.