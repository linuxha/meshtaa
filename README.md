# MeshVM - Meshtastic Virtual Machine Daemon

A Linux daemon that monitors Meshtastic messages via serial port and responds with data from MQTT topics.

## Features

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

### Quick Install

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
node_id = 123456789  # Your actual node ID

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
- `node_id`: Your Meshtastic node ID (required)

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

Open source - modify and distribute as needed.

## Support

For issues and feature requests, check the logs first, then verify your configuration matches your actual Meshtastic and MQTT setup.