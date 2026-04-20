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

- **Multi-Connection Support**: Connects to Meshtastic device via Serial, TCP/IP Network, or Bluetooth Low Energy (BLE)
- **Message Filtering**: Only processes messages directed to your specific node ID
- **Keyword Processing**: Responds to configurable keywords with MQTT topic data
- **User Greeting System**: Automatically greets new users who send broadcast messages (configurable format, 5-minute cache)
- **ID Filtering System**: Control which nodes the bot interacts with using allowlists or blocklists
- **MQTT Integration**: Caches data from MQTT topics for quick responses
- **Remote Message Sending**: Send messages via MQTT using MAC@message format
- **Daemon Operation**: Runs as a proper Linux daemon with systemd integration
- **Comprehensive Logging**: Detailed logging with configurable levels
- **Configuration Management**: Flexible configuration file support

## Architecture

```
┌─────────────────┐  Serial/Network/BLE  ┌─────────────────┐
│   Meshtastic    │◄─────────────────────┤    MeshVM       │
│     Device      │                      │    Daemon       │
└─────────────────┘                      └─────────────────┘
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
- Meshtastic device accessible via:
  - USB/Serial port (most common)
  - TCP/IP network connection (WiFi/Ethernet enabled devices)
  - Bluetooth Low Energy (BLE-capable devices)
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

Edit `/etc/meshvm/meshvm.conf`. Choose your connection type:

**Serial Connection (most common):**
```ini
[meshtastic]
connection_type = serial
serial_port = /dev/ttyUSB0
node_id = CE:6E:13:A3:20:93  # MAC address format (or use !13a32093 or 329457811)
```

**Network Connection (WiFi/Ethernet devices):**
```ini
[meshtastic]
connection_type = network
network_url = https://192.168.1.100:443
verify_ssl = false
node_id = CE:6E:13:A3:20:93
```

**Bluetooth Low Energy Connection:**
```ini
[meshtastic]
connection_type = bluetooth
bluetooth_mac = 01:23:45:67:89:AB  # Find with: bluetoothctl devices
node_id = CE:6E:13:A3:20:93
```

**Complete configuration example:**
```ini
[meshtastic]
connection_type = serial  # or 'network' or 'bluetooth'
serial_port = /dev/ttyUSB0
node_id = CE:6E:13:A3:20:93  # MAC address format (or use !13a32093 or 329457811)

[mqtt]
broker = localhost
port = 1883
username = your_mqtt_user
password = your_mqtt_password

[keywords]
# Keywords are configured without the # prefix
# Users must include # when sending messages (e.g., "#weather", "#status")
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

Additional
```bash
# python3 -m venv .venv #(One time)
# pip3 install pyserial paho-mqtt meshtastic (One time)
source .venv/bin/activate
# User needs to create this script to make sure MQTT topics are filled
bash ./t/mqtt-filler,sh
# Run in the foreground
python3 ./meshvm.py -f -c t/meshvm.conf
#pkill -f "meshvm.py"
deactivate
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
4. **Keyword Detection**: Scans incoming messages for configured keywords (must be prefixed with '#')
5. **Response**: When a keyword is found, retrieves cached MQTT data with automatic retry logic and sends response

### Example Interaction

A user can send a message directly to the Meshtastic node. A broadcast message will not work.

Keywords must be prefixed with '#' and are case-insensitive.

```
User sends: "Hey, what's the #weather today?"
MeshVM sees: "#weather" keyword pattern
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
- Keywords must be prefixed with '#' in messages (e.g., "#weather", "#status")
- Keywords are case-insensitive
- First matching keyword wins
- Automatic MQTT topic refresh with 3 retry attempts when cache expires

### Daemon Section
- `log_file`: Log file path
- `log_level`: DEBUG, INFO, WARNING, ERROR, CRITICAL
- `pid_file`: PID file location
- `message_topic`: MQTT topic for remote message sending (default: meshvm/send)
- `greeting_enabled`: Enable/disable auto-greeting new users (true/false)
- `greeting_format`: Customizable greeting message format with variables:
  - `{node_id}`: Full node ID (e.g., '!12345678')
  - `{node_id_short}`: Node ID without '!' prefix (e.g., '12345678')
  - `{bot_id}`: This bot's node ID

**Greeting Examples:**
```ini
# Default greeting
greeting_format = Hello {node_id}! Welcome to the mesh network!

# Personalized greeting
greeting_format = Welcome {node_id_short}! I'm bot {bot_id} 

# Simple greeting
greeting_format = New user {node_id} detected - hello from the mesh!
```

**Note**: Each user is greeted only once per 5-minute cache period to prevent spam.

### ID Filtering Configuration
Control which nodes the bot will interact with using filtering options:

```ini
[daemon]
filter_mode = none        # Filtering mode: none, allowlist, blocklist
filter_ids = ID1,ID2,ID3  # Comma-separated list of IDs to filter
```

**Supported ID formats:**
- **Hex node IDs**: `!12345678`, `!abcdef01`
- **MAC addresses**: `AA:BB:CC:DD:EE:FF`, `10:20:30:40:50:60`
- **Decimal IDs**: `305419896`, `2882400001`

**Filter modes:**
- `none`: No filtering (default) - respond to all users
- `allowlist`: Only respond to IDs in filter_ids list
- `blocklist`: Ignore IDs in filter_ids list, respond to everyone else

**Example configurations:**
```ini
# Block specific troublemakers
filter_mode = blocklist
filter_ids = !deadbeef, AA:BB:CC:DD:EE:FF, 305419896

# Only respond to authorized users
filter_mode = allowlist  
filter_ids = !12345678, !87654321, 10:20:30:40:50:60

# No filtering (respond to everyone)
filter_mode = none
filter_ids =
```

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

## Transmitting Messages to Meshtastic Nodes

MeshVM supports sending messages to Meshtastic nodes via MQTT. This allows external applications, scripts, or users to send messages through the Meshtastic mesh network using standard MQTT clients.

### Message Format

Messages are sent to the MQTT topic configured as `message_topic` (default: `meshvm/send_message`) using this format:

```
<MAC_ADDRESS>@<MESSAGE>
```

Where:
- `MAC_ADDRESS`: The target Meshtastic node's MAC address in format `XX:XX:XX:XX:XX:XX`
- `MESSAGE`: The text message to send
- `@`: Separator between MAC address and message

### Sending Messages with mosquitto_pub

#### Send to a Specific Node

```bash
# Send message to a specific Meshtastic node
mosquitto_pub -h localhost -t "meshvm/send_message" -m "CE:6E:13:A3:20:93@Hello from MQTT!"

# Send status request
mosquitto_pub -h localhost -t "meshvm/send_message" -m "10:20:BA:75:9C:D8@Please send your #status"

# Send multiple commands
mosquitto_pub -h localhost -t "meshvm/send_message" -m "AA:BB:CC:DD:EE:FF@Can you tell me the #weather and #temp?"
```

#### Broadcast to All Nodes

To send a message to all nodes on the mesh network, use either `*` or `FF:FF:FF:FF:FF:FF` as the MAC address:

```bash
# Broadcast using asterisk (recommended)
mosquitto_pub -h localhost -t "meshvm/send_message" -m "*@Emergency broadcast: All stations check in"

# Broadcast using broadcast MAC address
mosquitto_pub -h localhost -t "meshvm/send_message" -m "FF:FF:FF:FF:FF:FF@Network maintenance in 10 minutes"

# Broadcast a general announcement
mosquitto_pub -h localhost -t "meshvm/send_message" -m "*@Weekly mesh net starting now. Please join!"
```

### Message Length Limitations

⚠️ **Important**: Meshtastic has message length limitations that vary by radio preset:
- **Long/Fast presets**: ~200 characters maximum
- **Medium/Slow presets**: Even shorter limits

Long messages are automatically split into multiple parts with prefixes like `(1/3)`, `(2/3)`, `(3/3)`.

### Configuration

Ensure your `meshvm.conf` includes the message topic configuration:

```ini
[mqtt]
broker = localhost
port = 1883
message_topic = meshvm/send_message  # Topic for remote message requests
```

### Examples for Different Use Cases

#### Remote Monitoring and Control

```bash
# Request status from a remote station
mosquitto_pub -h localhost -t "meshvm/send_message" -m "12:34:56:78:9A:BC@Please send #battery and #status"

# Send configuration change notification
mosquitto_pub -h localhost -t "meshvm/send_message" -m "*@Configuration updated. Restart recommended."
```

#### Automated Alerting

```bash
#!/bin/bash
# Script example: Send weather alerts to all nodes
ALERT="Severe weather warning: High winds expected 8-10 PM. Secure equipment."
mosquitto_pub -h localhost -t "meshvm/send_message" -m "*@$ALERT"
```

#### Integration with Home Automation

```bash
# Home Assistant automation example
# Send doorbell notifications to mesh network
mosquitto_pub -h localhost -t "meshvm/send_message" -m "*@Visitor at front door - $(date '+%H:%M')"

# Send temperature alerts to specific monitoring stations
if [ $(cat /tmp/temp) -gt 35 ]; then
    mosquitto_pub -h localhost -t "meshvm/send_message" -m "AA:BB:CC:DD:EE:FF@High temperature alert: $(cat /tmp/temp)°C"
fi
```

### Finding Node MAC Addresses

To find MAC addresses of Meshtastic nodes in your network:

1. **From Meshtastic CLI**: `meshtastic --nodes`
2. **From device logs**: Check MeshVM history file for sender MAC addresses
3. **From network scans**: Use Bluetooth or network discovery tools
4. **Physical labels**: Many devices have MAC addresses printed on labels

### Error Handling

If a MAC address format is invalid or the target node is unreachable:
- Invalid formats will be logged as errors
- Messages to unreachable nodes may timeout silently
- Check MeshVM logs for transmission confirmations and errors

### Security Considerations

- Anyone with access to your MQTT broker can send messages through your Meshtastic node
- Consider using MQTT authentication (`username`/`password`) in production
- Monitor the message topic for unauthorized usage
- Be aware that broadcast messages are visible to all mesh network participants

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
   - **Ensure keywords are prefixed with '#' in messages** (e.g., "#weather")
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

## Version History

### v0.8.5 - Daemon Threading Fix
- **Threading Issue Fix**: Resolved "multi-threaded process fork()" DeprecationWarning
- **Daemon Stability**: Moved daemonization earlier in startup process before thread creation
- **Process Management**: Improved daemon startup sequence to prevent threading conflicts
- **Error Handling**: Enhanced error handling for daemon startup failures

### v0.8.4 - Enhanced Keyword Processing and Retry Logic
- **Keyword Format Change**: Keywords now require '#' prefix (e.g., "#weather", "#status")
- **MQTT Retry Logic**: Automatic topic refresh with up to 3 retry attempts when cache expires
- **Better Reliability**: System now attempts to refresh expired MQTT data before reporting unavailability
- **Enhanced Logging**: Detailed logging of retry attempts and refresh operations

### v0.8.2 - Cache Timeout Fix
- Connection Status Checking: When cache expires or is missing, the system now checks if the MQTT connection is still active
- Automatic Topic Refresh: When cache expires, the system re-subscribes to the specific topic to ensure fresh data reception
- Connection Recovery: Added automatic reconnection attempts when MQTT connection is lost
- Better Error Reporting: More specific error messages distinguish between "cache expired" and "connection lost" scenarios

### v0.7.0 - Network and Bluetooth support
- Adds Network (https) and Bluetooth support
- Fixes versioning (v0.6.x -> v0.7.x)

### v0.6.2 - Maintenance Release
- Patch version increment for repository synchronization
- Maintenance release with no functional changes

### v0.6.1 - Radio Interference Resilience
- Enhanced protobuf DecodeError handling to treat as warnings instead of errors
- Added monitoring loop exception recovery to continue operation despite intermittent failures
- Maintains daemon stability when Meshtastic radio experiences signal/interference issues
- Prevents unnecessary error logging for known communication degradation scenarios

### v0.6.0 - MQTT Remote Messaging
- Added remote messaging capability via MQTT using MAC@message format
- Supports sending messages to Meshtastic nodes from external MQTT clients
- Enhanced message validation and error handling for remote requests
- Configurable message_topic setting for remote message publishing

### v0.5.2 - Pubsub Interface Compatibility
- Fixed interface parameter mismatch in pubsub callback signature
- Improved compatibility with Meshtastic library pubsub interface
- Enhanced error logging for interface-related issues

### v0.5.1 - Message Chunking Fix
- Fixed character loss bug in message chunking algorithm
- Resolved issue where multi-part messages were missing characters
- Improved prefix length calculation before message splitting
- Enhanced message integrity for long responses

### v0.5.0 - Initial Release
- Basic serial port monitoring and message processing
- MQTT integration with topic caching
- Keyword-based auto-response system
- Daemon operation with systemd integration

## License

GPL 3.0 - Open source - modify and distribute as needed.

I'm a bit uncertain as to which Open Source License to use. At the moment I've chosen GPL 3.0.

## Support

For issues and feature requests, check the logs first, then verify your configuration matches your actual Meshtastic and MQTT setup.
