#!/usr/bin/env python3
"""
MeshVM - Meshtastic Virtual Machine Daemon

A Linux daemon that monitors Meshtastic messages via serial, network, or Bluetooth and responds with MQTT data.
The daemon:
- Connects to a Meshtastic device via serial port, TCP/IP network, or Bluetooth Low Energy (BLE)
- Monitors incoming TEXT messages directed to this node
- Processes messages for configurable keywords
- Retrieves cached data from MQTT topics
- Sends responses back to the original message sender
- Accepts message sending requests via MQTT for remote messaging

Architecture:
    Meshtastic Device <--> SerialInterface/TCPInterface/BLEInterface <--> MeshtasticMonitor
                                                                                  |
    MQTT Broker <--> MQTTManager <--> MeshVMDaemon <----------------------------------+
                                           |
                                    Configuration

Message Publishing:
    You can send messages to Meshtastic nodes by publishing to the configured 'message_topic'.
    Default topic: 'meshvm/send' (configurable in [daemon] section)
    
    Message Format: <MAC_ADDRESS>@<MESSAGE>
    - MAC_ADDRESS: Target node's MAC address in format XX:XX:XX:XX:XX:XX
    - MESSAGE: Text message to send to the target node
    - Use '*' or 'FF:FF:FF:FF:FF:FF' for broadcast messages
    
    Examples using mosquitto_pub:
    
    # Send direct message to a specific node
    mosquitto_pub -h mqtt.example.com -t "meshvm/send" \\
                  -m "10:20:BA:75:9C:D8@Hello from MQTT!"
    
    # Send broadcast message to all nodes
    mosquitto_pub -h mqtt.example.com -t "meshvm/send" \\
                  -m "*@Network announcement: System maintenance in 5 minutes"
    
    # With authentication
    mosquitto_pub -h mqtt.example.com -u username -P password \\
                  -t "meshvm/send" \\
                  -m "AA:BB:CC:DD:EE:FF@Status update from server"
    
    # Monitor the message topic (for debugging)
    mosquitto_sub -h mqtt.example.com -t "meshvm/send" -v

Greeting Configuration:
    MeshVM can automatically greet new users who send broadcast messages on the mesh network.
    Configure greeting behavior in the [daemon] section of meshvm.conf:
    
    [daemon]
    greeting_enabled = true                                   # Enable/disable greeting feature
    greeting_format = Hello {node_id}! Welcome to the mesh!  # Customizable greeting message
    
    Available format variables in greeting_format:
    - {node_id}      : Full node ID (e.g., '!12345678')
    - {node_id_short}: Node ID without '!' prefix (e.g., '12345678') 
    - {bot_id}       : This bot's node ID (e.g., '!87654321')
    
    Example configurations:
    - greeting_format = Hello {node_id}! Welcome to the mesh network!
    - greeting_format = Welcome {node_id_short}! I'm bot {bot_id}
    - greeting_format = New user {node_id} detected - hello from the mesh!
    
    Note: Each user is greeted only once per 5-minute cache period to prevent spam.

ID Filtering:
    Configure user filtering to control which nodes the bot will interact with.
    Useful for blocking spam or limiting responses to authorized users only.
    
    [daemon]
    filter_mode = none        # Filtering mode: none, allowlist, blocklist
    filter_ids = ID1,ID2,ID3  # Comma-separated list of IDs to filter
    
    Supported ID formats:
    - Hex node IDs:    !12345678, !abcdef01
    - MAC addresses:   AA:BB:CC:DD:EE:FF, 10:20:30:40:50:60
    - Decimal IDs:     305419896, 2882400001
    
    Filter modes:
    - none:      No filtering (default) - respond to all users
    - allowlist: Only respond to IDs in filter_ids list
    - blocklist: Ignore IDs in filter_ids list, respond to everyone else
    
    Example configurations:
    # Block specific troublemakers
    filter_mode = blocklist
    filter_ids = !deadbeef, AA:BB:CC:DD:EE:FF, 305419896
    
    # Only respond to authorized users
    filter_mode = allowlist  
    filter_ids = !12345678, !87654321, 10:20:30:40:50:60

Author: Senior Software Engineer
Date: February 2026
Version: 0.12.0
"""

__version__ = "0.12.0"

import sys
import os
import time
import json
import logging
import re
import signal
import threading
import configparser
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

# Import only basic libraries that don't create threads
# Threading-related imports will be deferred until after daemon fork
try:
    import configparser
    import logging.handlers
    import ssl
    from urllib.parse import urlparse
except ImportError as e:
    print(f"Required dependency missing: {e}")
    print("Install with: pip install pyserial paho-mqtt meshtastic")
    sys.exit(1)

# Global variables for deferred imports (set after daemon fork)
mqtt = None
urllib3 = None
SerialInterface = None
TCPInterface = None
BLEInterface = None
mesh_pb2 = None
portnums_pb2 = None
meshtastic = None
traceback = None

def _import_threading_libraries():
    """
    Import libraries that create threads during initialization.
    
    This function must be called AFTER daemon fork() to avoid the 
    DeprecationWarning about multi-threaded fork().
    
    Libraries that create threads:
    - paho.mqtt.client: Creates internal threads for connection management
    - meshtastic: May create threads for device communication
    - urllib3: Thread pool for HTTP connections
    """
    global mqtt, urllib3, SerialInterface, TCPInterface, BLEInterface
    global mesh_pb2, portnums_pb2, meshtastic, traceback
    
    try:
        import paho.mqtt.client as mqtt
        import urllib3
        import traceback
        
        from meshtastic.serial_interface import SerialInterface 
        from meshtastic.tcp_interface import TCPInterface
        from meshtastic.ble_interface import BLEInterface
        from meshtastic import mesh_pb2
        from meshtastic.protobuf import portnums_pb2
        import meshtastic
        
        # Disable SSL warnings for certificate verification bypass
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
    except ImportError as e:
        raise ImportError(f"Required dependency missing: {e}. Install with: pip install pyserial paho-mqtt meshtastic")
    except Exception as e:
        raise

#
#

class MeshVMConfig:
    """
    Configuration manager for MeshVM daemon
    
    Handles loading, parsing, and accessing configuration values from INI files.
    Provides defaults for all required settings and supports path expansion.
    """
    
    def __init__(self, config_path: str = "/etc/meshvm/meshvm.conf"):
        """Initialize configuration manager with config file path"""
        self.config_path = config_path
        self.config = configparser.ConfigParser()
        self.load_config()
    
    def load_config(self):
        """
        Load configuration from file with comprehensive defaults
        
        Sets up default values for all required configuration sections:
        - meshtastic: Serial port settings and node identification
        - mqtt: MQTT broker connection settings
        - daemon: Logging and process management
        - keywords: Keyword-to-MQTT-topic mappings
        """
        # Set Meshtastic device defaults
        self.config.add_section('meshtastic')
        self.config.set('meshtastic', 'connection_type', 'serial')  # 'serial', 'network', or 'bluetooth'
        self.config.set('meshtastic', 'serial_port', '/dev/ttyUSB0')
        self.config.set('meshtastic', 'baudrate', '115200')
        self.config.set('meshtastic', 'network_url', '')  # e.g., https://hostname:9443/
        self.config.set('meshtastic', 'verify_ssl', 'false')  # SSL certificate verification
        self.config.set('meshtastic', 'bluetooth_mac', '')  # Bluetooth Low Energy MAC address (e.g., 01:23:45:67:89:AB)
        self.config.set('meshtastic', 'bluetooth_pin', '')  # Optional Bluetooth PIN for pairing (rarely needed)
        self.config.set('meshtastic', 'node_id', '')  # Must be set by user - hex format (!12345678) or MAC address
        
        self.config.add_section('mqtt')
        self.config.set('mqtt', 'broker', 'localhost')
        self.config.set('mqtt', 'port', '1883')
        self.config.set('mqtt', 'username', '')
        self.config.set('mqtt', 'password', '')
        self.config.set('mqtt', 'keepalive', '60')
        
        self.config.add_section('daemon')
        self.config.set('daemon', 'log_file', '/var/log/meshvm.log')
        self.config.set('daemon', 'log_level', 'INFO')
        self.config.set('daemon', 'pid_file', '/var/run/meshvm.pid')
        self.config.set('daemon', 'history_file', '/var/log/meshvm_history.md')
        self.config.set('daemon', 'message_topic', 'meshvm/send')  # Topic for sending messages via MQTT
        self.config.set('daemon', 'protobuf_resilience', 'true')  # Enhanced protobuf error handling
        self.config.set('daemon', 'greeting_format', 'Hello {node_id}! Welcome to the mesh network!')  # Greeting message format
        self.config.set('daemon', 'greeting_enabled', 'true')  # Enable/disable greeting new users
        self.config.set('daemon', 'filter_mode', 'none')  # Filter mode: none, allowlist, blocklist
        self.config.set('daemon', 'filter_ids', '')  # Comma-separated list of IDs to filter (hex, MAC, decimal)
        
        self.config.add_section('keywords')
        self.config.set('keywords', 'weather', 'sensors/weather')
        self.config.set('keywords', 'status', 'system/status')
        self.config.set('keywords', 'temp', 'sensors/temperature')
        self.config.set('keywords', 'ping', 'system/ping')
        
        # Try to load from file
        if os.path.exists(self.config_path):
            self.config.read(self.config_path)
    
    def get(self, section: str, option: str, fallback: str = '') -> str:
        """Get configuration value with fallback default"""
        return self.config.get(section, option, fallback=fallback)
    
    def getint(self, section: str, option: str, fallback: int = 0) -> int:
        """Get configuration integer value with fallback default"""
        return self.config.getint(section, option, fallback=fallback)
    
    def get_keywords(self) -> dict:
        """
        Get keywords dictionary mapping keywords to MQTT topics
        
        Returns:
            Dictionary of {keyword: mqtt_topic} mappings from config [keywords] section
        """
        if self.config.has_section('keywords'):
            return dict(self.config.items('keywords'))
        return {}
    
    def create_sample_config(self):
        """
        Create a sample configuration file with current defaults
        
        Creates directory structure if needed and writes current configuration
        with all default values to the specified config file path.
        """
        config_dir = Path(self.config_path).parent
        config_dir.mkdir(parents=True, exist_ok=True)
        
        with open(self.config_path, 'w') as f:
            self.config.write(f)


class MQTTManager:
    """
    MQTT client manager for retrieving and caching topic data
    
    Responsibilities:
    - Connect to MQTT broker with authentication
    - Subscribe to all configured keyword topics
    - Cache received messages with timestamps
    - Provide cached data lookup with expiration handling
    - Handle connection events and reconnection
    
    Data Flow:
    1. Connect to broker and subscribe to all keyword topics
    2. Cache incoming messages with timestamps
    3. Serve cached data to keyword processors
    4. Automatically expire old cache entries
    """
    
    def __init__(self, config: MeshVMConfig, logger: logging.Logger):
        """Initialize MQTT manager with configuration and logger"""
        self.config = config
        self.logger = logger
        # Fix deprecation warning by using callback API version 2
        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.connected = False
        self.topic_cache = {}  # Cache: {topic: {payload: str, timestamp: float}}
        self.cache_timeout = 300  # Cache expiration: 5 minutes
        self.message_callback = None  # Callback for message sending requests
        
        # Setup MQTT client callbacks for connection lifecycle
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        
        # Setup MQTT authentication if credentials provided
        username = self.config.get('mqtt', 'username')
        password = self.config.get('mqtt', 'password')
        if username and password:
            self.client.username_pw_set(username, password)
    
    def connect(self):
        """
        Connect to MQTT broker and start message loop
        
        Attempts to establish connection to configured MQTT broker.
        Logs errors but doesn't raise exceptions to allow daemon startup for testing.
        """
        try:
            broker = self.config.get('mqtt', 'broker')
            port = self.config.getint('mqtt', 'port', 1883)
            keepalive = self.config.getint('mqtt', 'keepalive', 60)
            
            self.logger.info(f"Connecting to MQTT broker {broker}:{port}")
            self.client.connect(broker, port, keepalive)
            self.client.loop_start()
        except Exception as e:
            self.logger.error(f"Failed to connect to MQTT broker: {e}")
            self.logger.warning("Daemon will continue without MQTT functionality for testing purposes")
            # Don't raise the exception - allow daemon to continue for testing
    
    def disconnect(self):
        """Disconnect from MQTT broker and stop message loop"""
        if self.connected:
            self.client.loop_stop()
            self.client.disconnect()
    
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        """
        MQTT connection callback - handles successful connections and subscriptions
        
        Args:
            client: MQTT client instance
            userdata: User data (unused)
            flags: Connection flags
            reason_code: Connection reason code (0 = success for v2 API)
            properties: Connection properties
        """
        broker = self.config.get('mqtt', 'broker')
        port = self.config.getint('mqtt', 'port', 1883)
        
        if reason_code == 0:
            self.connected = True
            self.logger.info(f"Connected to MQTT broker {broker}:{port}")
            
            # Subscribe to all configured keyword topics for data caching
            keywords = self.config.get_keywords()
            self.logger.debug(f"MQTT Subscription - Server: {broker}:{port}, Keywords: {len(keywords)} topics")
            
            for keyword, topic in keywords.items():
                result, mid = client.subscribe(topic)
                self.logger.info(f"MQTT Subscribed - Topic: '{topic}', Keyword: '{keyword}', Result: {result}")
            
            # Subscribe to message sending topic
            message_topic = self.config.get('daemon', 'message_topic', 'meshvm/send')
            result, mid = client.subscribe(message_topic)
            self.logger.info(f"MQTT Subscribed - Message Topic: '{message_topic}', Result: {result}")
        else:
            self.logger.error(f"Failed to connect to MQTT broker {broker}:{port}, reason code {reason_code}")
    
    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        """
        MQTT disconnection callback - handles connection loss
        
        Args:
            client: MQTT client instance
            userdata: User data (unused)
            disconnect_flags: Disconnection flags
            reason_code: Disconnection reason code
            properties: Disconnection properties
        """
        self.connected = False
        self.logger.warning(f"Disconnected from MQTT broker, reason code {reason_code}")
    
    def _on_message(self, client, userdata, message):
        """
        MQTT message callback - caches incoming messages with timestamps
        
        Args:
            client: MQTT client instance
            userdata: User data (unused)
            message: MQTT message object with topic and payload
        """
        broker = self.config.get('mqtt', 'broker')
        port = self.config.getint('mqtt', 'port', 1883)
        topic = message.topic
        payload = message.payload.decode('utf-8')
        timestamp = time.time()
        
        # Check if this is a message sending request
        message_topic = self.config.get('daemon', 'message_topic', 'meshvm/send')
        if topic == message_topic:
            self._handle_message_request(payload)
            return
        
        # Store message in cache with timestamp for expiration handling
        self.topic_cache[topic] = {
            'payload': payload,
            'timestamp': timestamp
        }
        
        self.logger.info(f"MQTT Data Updated - Topic: '{topic}', Payload: '{payload[:50]}...', Cache size: {len(self.topic_cache)}")
        self.logger.debug(f"MQTT Message Received - Server: {broker}:{port}, Topic: '{topic}', Payload: '{payload[:100]}...', Cache size: {len(self.topic_cache)}")
    
    def get_topic_data(self, topic: str) -> Optional[str]:
        """
        Get cached data for a topic with expiration checking
        
        Args:
            topic: MQTT topic name to look up
            
        Returns:
            Cached message payload if found and not expired, None otherwise
            
        Cache Logic:
        1. Check if topic exists in cache
        2. Verify cache entry is not expired (5 minute timeout)
        3. Return payload if valid, remove if expired
        4. Log all cache operations for debugging
        5. Check MQTT connection status when cache is expired/missing
        """
        broker = self.config.get('mqtt', 'broker')
        port = self.config.getint('mqtt', 'port', 1883)
        
        self.logger.debug(f"MQTT Data Lookup - Server: {broker}:{port}, Topic: '{topic}'")
        
        if topic in self.topic_cache:
            cache_entry = self.topic_cache[topic]
            # Check if cache is still valid (not expired)
            cache_age = time.time() - cache_entry['timestamp']
            if cache_age < self.cache_timeout:
                self.logger.debug(f"MQTT Cache Hit - Topic: '{topic}', Data: '{cache_entry['payload'][:100]}...', Age: {cache_age:.1f}s")
                return cache_entry['payload']
            else:
                # Remove expired cache entry to free memory
                self.logger.debug(f"MQTT Cache Expired - Topic: '{topic}', Age: {cache_age:.1f}s (timeout: {self.cache_timeout}s)")
                del self.topic_cache[topic]
                
                # Check MQTT connection and re-subscribe to topic if disconnected
                self._check_and_refresh_topic(topic)
        
        # Check connection status when cache miss occurs
        if not self.connected:
            self.logger.warning(f"MQTT Cache Miss - Server: {broker}:{port}, Topic: '{topic}', MQTT connection lost - attempting reconnection")
            self._attempt_reconnect()
        else:
            self.logger.debug(f"MQTT Cache Miss - Server: {broker}:{port}, Topic: '{topic}', Cache size: {len(self.topic_cache)}, Connection: OK")
        
        return None
    
    def _check_and_refresh_topic(self, topic: str):
        """
        Check MQTT connection and re-subscribe to topic when cache expires
        
        Args:
            topic: MQTT topic to refresh subscription for
        """
        if self.connected:
            # Re-subscribe to the specific topic to ensure we get fresh data
            result, mid = self.client.subscribe(topic)
            self.logger.info(f"MQTT Topic Refresh - Re-subscribed to '{topic}', Result: {result}")
        else:
            self.logger.warning(f"MQTT Topic Refresh Failed - Topic: '{topic}', Connection lost")
            
    def _attempt_reconnect(self):
        """
        Attempt to reconnect to MQTT broker when connection is lost
        """
        try:
            broker = self.config.get('mqtt', 'broker')
            port = self.config.getint('mqtt', 'port', 1883)
            self.logger.info(f"MQTT Reconnection Attempt - Server: {broker}:{port}")
            
            # Stop current loop and reconnect
            self.client.loop_stop()
            self.client.reconnect()
            self.client.loop_start()
            
        except Exception as e:
            self.logger.error(f"MQTT Reconnection Failed - Error: {e}")
    
    def set_message_callback(self, callback):
        """Set callback function for handling message sending requests"""
        self.message_callback = callback
    
    def _handle_message_request(self, payload: str):
        """
        Handle message sending request from MQTT topic
        
        Processes MQTT messages to send text messages to Meshtastic nodes.
        This enables remote messaging via MQTT publish commands.
        
        Message Format: <MAC_ADDRESS>@<MESSAGE>
        - MAC_ADDRESS: Target node's MAC address (XX:XX:XX:XX:XX:XX)
        - MESSAGE: Text message content to send
        - Use '*' or 'FF:FF:FF:FF:FF:FF' for broadcast messages
        
        Examples:
            "10:20:BA:75:9C:D8@Hello from server!"
            "*@Network announcement: Maintenance starting"
            "AA:BB:CC:DD:EE:FF@Status check - please respond"
        
        MQTT Publishing Examples:
            # Direct message to specific node
            mosquitto_pub -h broker.local -t "meshvm/send" \\
                         -m "10:20:BA:75:9C:D8@Hello from MQTT!"
            
            # Broadcast message to all nodes  
            mosquitto_pub -h broker.local -t "meshvm/send" \\
                         -m "*@System update available"
        
        Args:
            payload: MQTT message payload in format MAC@message
        """
        try:
            if '@' not in payload:
                self.logger.warning(f"Message format invalid - missing '@' separator: {payload}")
                return
            
            mac_addr, message = payload.split('@', 1)  # Split on first @ only
            mac_addr = mac_addr.strip().upper()
            message = message.strip()
            
            if not message:
                self.logger.warning(f"Empty message content for MAC {mac_addr}")
                return
            
            # Validate MAC address format (allow broadcast addresses)
            if mac_addr == '*' or mac_addr == 'FF:FF:FF:FF:FF:FF':
                # Accept broadcast addresses
                pass
            elif not re.match(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$', mac_addr):
                self.logger.warning(f"Invalid MAC address format: {mac_addr}")
                return
            
            self.logger.info(f"Message Send Request - MAC: {mac_addr}, Message: '{message}'")
            
            # Convert MAC to node ID and send via callback
            if self.message_callback:
                self.message_callback(mac_addr, message)
            else:
                self.logger.warning("No message callback registered - cannot send message")
                
        except Exception as e:
            self.logger.error(f"Error processing message request '{payload}': {e}")


class MeshtasticMonitor:
    """
    Meshtastic serial port monitor and message processor
    
    Responsibilities:
    - Connect to Meshtastic device via serial interface
    - Monitor incoming TEXT_MESSAGE_APP messages
    - Filter messages directed to this node's ID
    - Apply user ID filtering (allowlist/blocklist) 
    - Process messages for configured keywords
    - Send responses back to message senders
    - Log all interactions to history file
    """
    
    def __init__(self, config: MeshVMConfig, mqtt_manager: MQTTManager, logger: logging.Logger):
        """Initialize Meshtastic monitor with dependencies"""
        self.config = config
        self.mqtt_manager = mqtt_manager
        self.logger = logger
        self.interface = None  # SerialInterface instance
        self.my_node_id = None  # This node's numeric ID
        self.running = False  # Monitor thread control flag
        self.history_file = None  # Chat history log file path
        
        # New user greeting system
        self.greeted_users = {}  # Cache of greeted users {node_id: timestamp}
        self.greeting_cache_duration = 300  # 5 minutes in seconds
        
        # ID filtering system
        self.filter_mode = self.config.get('daemon', 'filter_mode', 'none').lower()  # none, allowlist, blocklist
        self.filtered_ids = self._load_filter_ids()  # Set of normalized IDs for filtering
        
        # Protobuf error tracking for restart mechanism
        self.protobuf_error_count = 0  # Count of protobuf parsing errors
        self.error_window_start = time.time()  # Start of current error tracking window
        self.error_window_duration = 300  # 5 minute window for error tracking
        self.max_errors_per_window = 50  # Max errors before restart
        self.restart_requested = False  # Flag to request daemon restart
        
        # Register callback for MQTT message sending requests
        self.mqtt_manager.set_message_callback(self._handle_mqtt_message_request)
    
    def _setup_history_logging(self):
        """
        Setup chat history logging to Markdown file
        
        Creates history file if it doesn't exist with proper header.
        History file logs all message interactions for debugging and record-keeping.
        """
        self.history_file = os.path.expanduser(self.config.get('daemon', 'history_file', '/var/log/meshvm_history.md'))
        
        # Create history directory if it doesn't exist
        history_dir = Path(self.history_file).parent
        history_dir.mkdir(parents=True, exist_ok=True)
        
        # Create history file with header if it doesn't exist
        if not os.path.exists(self.history_file):
            with open(self.history_file, 'w') as f:
                f.write(f"# MeshVM Chat History\n\n")
                f.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Version: {__version__}\n\n")
        
        self.logger.info(f"Chat history logging to: {self.history_file}")
    
    def _log_to_history(self, message_type: str, sender_id: str, message: str, response: str = None):
        """
        Log chat interaction to history file in Markdown format
        
        Args:
            message_type: Type of message (e.g., 'received')
            sender_id: Node ID of the message sender
            message: Original message text
            response: Response sent back (None if no keyword matched)
            
        Creates timestamped entries with sender, message, and response information.
        """
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            with open(self.history_file, 'a') as f:
                f.write(f"## {timestamp}\n\n")
                f.write(f"**From Node:** {sender_id}\n\n")
                f.write(f"**Message:** {message}\n\n")
                
                if response:
                    f.write(f"**Response:** {response}\n\n")
                else:
                    f.write(f"**Response:** *(No keyword match)*\n\n")
                
                f.write("---\n\n")
                
        except Exception as e:
            self.logger.error(f"Failed to log to history file: {e}")
    
    def _mac_to_node_id(self, mac_address):
        """Convert MAC address to Meshtastic node ID.
        
        Takes the last 4 octets of a MAC address, removes colons,
        and converts to a hex string for use as node_id.
        Supports broadcast addresses '*' and 'FF:FF:FF:FF:FF:FF'.
        
        Example: CE:6E:13:A3:20:93 -> !13a32093
                 * -> ^all
                 FF:FF:FF:FF:FF:FF -> ^all
        
        Args:
            mac_address: MAC address string (e.g., "CE:6E:13:A3:20:93") or broadcast ('*', 'FF:FF:FF:FF:FF:FF')
            
        Returns:
            str: Node ID in hex format (e.g., "!13a32093") or "^all" for broadcast
            
        Raises:
            ValueError: If MAC address format is invalid
        """
        # Remove any whitespace and convert to uppercase
        mac_clean = mac_address.replace(' ', '').upper()
        
        # Handle broadcast addresses
        if mac_clean == '*' or mac_clean == 'FF:FF:FF:FF:FF:FF':
            self.logger.info(f"Converted broadcast address {mac_address} -> node_id: ^all")
            return "^all"
        
        # Validate MAC address format
        if not re.match(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$', mac_clean):
            raise ValueError(f"Invalid MAC address format: {mac_address}")
        
        # Split into octets and take the last 4
        octets = mac_clean.split(':')
        last_four = octets[-4:]
        
        # Join without colons and convert to lowercase
        node_hex = ''.join(last_four).lower()
        
        self.logger.info(f"Converted MAC {mac_address} -> node_id: !{node_hex}")
        return f"!{node_hex}"
    
    def _normalize_node_id(self, node_id: str) -> str:
        """
        Normalize node ID to consistent hex format for filtering
        
        Supports multiple input formats:
        - Hex format: !12345678 -> 12345678
        - MAC address: AA:BB:CC:DD:EE:FF -> ddccddff (last 4 octets)
        - Decimal: 305419896 -> 12345678
        
        Args:
            node_id: Node ID in various formats
            
        Returns:
            str: Normalized hex string (lowercase, no prefix)
        """
        try:
            node_id = str(node_id).strip()
            
            # Handle hex format (!12345678)
            if node_id.startswith('!'):
                return node_id[1:].lower()
            
            # Handle MAC address format (AA:BB:CC:DD:EE:FF)
            if re.match(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$', node_id):
                octets = node_id.upper().split(':')
                last_four = octets[-4:]
                return ''.join(last_four).lower()
            
            # Handle decimal format
            if node_id.isdigit():
                return f"{int(node_id):08x}"
            
            # Assume already normalized hex
            return node_id.lower()
            
        except Exception as e:
            self.logger.warning(f"Failed to normalize node ID '{node_id}': {e}")
            return node_id.lower()
    
    def _load_filter_ids(self) -> set:
        """
        Load and normalize filter IDs from configuration
        
        Returns:
            set: Set of normalized hex node IDs
        """
        filter_ids_str = self.config.get('daemon', 'filter_ids', '')
        if not filter_ids_str.strip():
            return set()
        
        filter_ids = set()
        raw_ids = [id_str.strip() for id_str in filter_ids_str.split(',') if id_str.strip()]
        
        for raw_id in raw_ids:
            normalized = self._normalize_node_id(raw_id)
            filter_ids.add(normalized)
            self.logger.debug(f"Added filter ID: {raw_id} -> {normalized}")
        
        if filter_ids:
            self.logger.info(f"Loaded {len(filter_ids)} filter IDs in {self.filter_mode} mode")
        
        return filter_ids
    
    def _is_id_filtered(self, from_id: int, sender_id: str) -> bool:
        """
        Check if a node ID should be filtered based on configuration
        
        Args:
            from_id: Numeric node ID
            sender_id: String node ID (e.g., '!12345678')
            
        Returns:
            bool: True if message should be filtered (ignored), False if allowed
        """
        if self.filter_mode == 'none' or not self.filtered_ids:
            return False  # No filtering
        
        # Normalize the sender ID for comparison
        normalized_id = self._normalize_node_id(sender_id)
        
        if self.filter_mode == 'allowlist':
            # Only allow IDs in the list
            allowed = normalized_id in self.filtered_ids
            if not allowed:
                self.logger.debug(f"ID {sender_id} not in allowlist - filtering")
            return not allowed
        
        elif self.filter_mode == 'blocklist':
            # Block IDs in the list
            blocked = normalized_id in self.filtered_ids
            if blocked:
                self.logger.debug(f"ID {sender_id} in blocklist - filtering")
            return blocked
        
        return False  # Default: don't filter
    
    def connect(self):
        """
        Connect to Meshtastic device via serial, network, or Bluetooth Low Energy interface
        
        Connection Types:
        - Serial: Direct USB/UART connection via serial port (most reliable)
        - Network: TCP/IP connection over WiFi/Ethernet (requires device web interface)
        - Bluetooth: BLE connection for mobile/wireless scenarios (requires BLE support)
        
        Connection process:
        1. Connect to device using configured connection type (serial/network/bluetooth)
        2. Setup chat history logging to markdown file
        3. Determine this node's ID from device info or config file
        4. Validate node ID is properly configured for message filtering
        
        Bluetooth Notes:
        - Uses Bluetooth Low Energy (BLE) not classic Bluetooth
        - Requires bluetooth_mac setting with device's BLE MAC address
        - PIN usually not required for modern devices (handled during initial pairing)
        - May require device to be in pairing mode for first connection
        
        Raises:
            Exception: If device connection fails or node ID cannot be determined
        """
        try:
            connection_type = self.config.get('meshtastic', 'connection_type', 'serial')
            
            if connection_type.lower() == 'network':
                network_url = self.config.get('meshtastic', 'network_url')
                if not network_url:
                    raise Exception("Network URL is required when connection_type is 'network'")
                
                # Parse the URL to extract hostname and port
                parsed_url = urlparse(network_url)
                hostname = parsed_url.hostname
                port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)
                
                self.logger.info(f"Connecting to Meshtastic device via network: {hostname}:{port}")
                
                # Configure SSL context to disable certificate verification if requested
                verify_ssl = self.config.get('meshtastic', 'verify_ssl', 'false').lower() == 'true'
                if not verify_ssl:
                    self.logger.info("SSL certificate verification disabled")
                
                # Create TCP interface (Meshtastic library handles the connection)
                self.interface = TCPInterface(hostname=hostname, portNumber=port)
            elif connection_type.lower() == 'bluetooth':
                bluetooth_mac = self.config.get('meshtastic', 'bluetooth_mac')
                if not bluetooth_mac:
                    raise Exception("Bluetooth MAC address is required when connection_type is 'bluetooth'")
                
                bluetooth_pin = self.config.get('meshtastic', 'bluetooth_pin')
                
                self.logger.info(f"Connecting to Meshtastic device via Bluetooth Low Energy (BLE): {bluetooth_mac}")
                if bluetooth_pin:
                    self.logger.info("Bluetooth PIN provided for authentication (rarely needed for BLE)")
                    self.logger.warning("Note: Most modern BLE devices handle pairing automatically")
                else:
                    self.logger.info("No Bluetooth PIN provided - using standard BLE connection (recommended)")
                
                # Create BLE interface - PIN typically handled at OS pairing level, not application level
                if bluetooth_pin:
                    # Note: PIN handling varies by meshtastic library version and device
                    # Most BLE devices handle authentication at the OS bluetooth stack level
                    # PIN parameter may be ignored by BLE interface implementation
                    self.logger.info(f"Using Bluetooth PIN: {bluetooth_pin[:3]}*** (may be ignored by BLE stack)")
                    self.interface = BLEInterface(address=bluetooth_mac)
                else:
                    self.interface = BLEInterface(address=bluetooth_mac)
                
                self.logger.info("BLE connection established - if connection fails, ensure device is paired with system Bluetooth")
            else:
                # Default to serial connection
                serial_port = self.config.get('meshtastic', 'serial_port')
                self.logger.info(f"Connecting to Meshtastic device via serial: {serial_port}")
                
                self.interface = SerialInterface(serial_port)
            
            # Setup history logging
            self._setup_history_logging()
            
            # Get our node ID from device or configuration
            node_info = self.interface.getMyNodeInfo()
            if node_info:
                self.my_node_id = node_info.get('num')
                hex_format = f"!{self.my_node_id:08x}"
                self.logger.info(f"Connected to Meshtastic device")
                self.logger.info(f"Node ID - Config format (hex): {hex_format}")
                self.logger.info(f"Node ID - Internal format (decimal): {self.my_node_id}")
            else:
                # Fallback to configured node ID
                configured_id = self.config.get('meshtastic', 'node_id')
                if configured_id:
                    self.logger.info(f"Node ID - Config format from file: {configured_id}")
                    
                    # Check if it's a MAC address format (XX:XX:XX:XX:XX:XX)
                    if re.match(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$', configured_id.strip()):
                        self.logger.info(f"Detected MAC address format: {configured_id}")
                        configured_id = self._mac_to_node_id(configured_id.strip())
                        self.logger.info(f"Converted to node_id format: {configured_id}")
                    
                    # Handle hex format (e.g., !146b40f5) or decimal
                    if configured_id.startswith('!'):
                        self.my_node_id = int(configured_id[1:], 16)
                        self.logger.info(f"Node ID - Converted from hex format")
                    else:
                        self.my_node_id = int(configured_id)
                        self.logger.info(f"Node ID - Using decimal format from config")
                    hex_format = f"!{self.my_node_id:08x}"
                    self.logger.info(f"Node ID - Config format (hex): {hex_format}")
                    self.logger.info(f"Node ID - Internal format (decimal): {self.my_node_id}")
                else:
                    raise Exception("Could not determine node ID")
            
        except Exception as e:
            self.logger.error(f"Failed to connect to Meshtastic device: {e}")
            raise
    
    def disconnect(self):
        """Disconnect from Meshtastic device and cleanup resources"""
        if self.interface:
            self.interface.close()
            self.logger.info("Disconnected from Meshtastic device")
    
    def start_monitoring(self):
        """
        Start monitoring for incoming Meshtastic messages
        
        Uses the Meshtastic publish/subscribe system to receive messages.
        Runs in main thread and blocks until monitoring is stopped.
        
        Message Processing Flow:
        1. Subscribe to Meshtastic message events
        2. Run monitoring loop (sleeps until messages arrive)
        3. Handle KeyboardInterrupt for graceful shutdown
        4. Cleanup monitoring state on exit
        """
        self.running = True
        self.logger.info("Starting message monitoring")
        
        # Set up message handler using Meshtastic pub/sub system
        import meshtastic
        meshtastic.pub.subscribe(self._on_receive_message, "meshtastic.receive")
        
        # Keep the monitoring thread alive until stopped
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("Monitoring interrupted")
        except Exception as e:
            # Handle unexpected errors during monitoring
            self.logger.error(f"Monitoring error: {e}")
            # Continue running unless explicitly stopped
            if self.running:
                self.logger.info("Continuing monitoring despite error...")
                time.sleep(5)  # Brief pause before continuing
        finally:
            self.running = False
    
    def stop_monitoring(self):
        """Stop message monitoring and set running flag to False"""
        self.running = False
        self.logger.info("Stopping message monitoring")
    
    def _on_receive_message(self, packet, interface=None):
        """
        Handle received Meshtastic message from pub/sub system
        
        Message Filtering Process:
        1. Validate packet is a dictionary
        2. Extract sender and destination node IDs
        3. Check if message is directed to this node OR is a broadcast
        4. Verify message is TEXT_MESSAGE_APP type
        5. Process new user greetings for broadcast messages
        6. Process message for keywords if directed to us
        7. Log interaction to history file
        
        Args:
            packet: Meshtastic message packet dictionary
            interface: Meshtastic interface instance (optional, provided by pubsub)
        """

        self.logger.debug(f"xxx on_Received message")

        try:
            # Validate packet format
            if not isinstance(packet, dict):
                return
            
            # Extract sender and destination info
            from_id     = packet.get('from', 0)
            to_id       = packet.get('to', 0)
            from_id_str = packet.get('fromId', '')
            to_id_str   = packet.get('toId', '')
            
            # Skip messages from ourselves
            if from_id == self.my_node_id:
                return
            
            # Check if this is a broadcast message (0xFFFFFFFF)
            is_broadcast = (to_id == 0xFFFFFFFF) or (to_id_str == '^all')
            
            # Check if message is for us (compare numeric IDs)
            is_for_us = False
            if to_id == self.my_node_id:
                is_for_us = True
            else:
                # Also check string format as fallback
                expected_id_str = f'!{self.my_node_id:08x}'
                if to_id_str == expected_id_str:
                    is_for_us = True
            
            # Only process if message is for us OR is a broadcast
            if not is_for_us and not is_broadcast:
                return  # Message not for us and not broadcast
            
            # Apply ID filtering - check sender against filter list
            sender_id_str = from_id_str if from_id_str else f'!{from_id:08x}'
            if self._is_id_filtered(from_id, sender_id_str):
                self.logger.debug(f"Message from {sender_id_str} filtered by {self.filter_mode} - ignoring")
                return  # Sender is filtered out
            
            # Extract message content
            decoded = packet.get('decoded', {})
            self.logger.info(f"xxx Received yyy");
            if not decoded:
                return
            
            portnum = decoded.get('portnum')
            
            # Only process TEXT_MESSAGE_APP messages
            if portnum != 'TEXT_MESSAGE_APP':
                return
                
            # Get the text content from payload field
            message_payload = decoded.get('payload')
            tmsg_payload    = decoded.get('text')

            self.logger.debug(f"xxx Received message: {message_payload} {tmsg_payload}")
            self.logger.info(f"xxx Received message: {message_payload} {tmsg_payload}")

            if not message_payload:
                return
                
            # Handle both string and bytes payload
            if isinstance(message_payload, bytes):
                message_text = message_payload.decode('utf-8', errors='ignore')
            else:
                message_text = str(message_payload)
                
            message_text_lower = message_text.strip().lower()
            sender_id = from_id_str if from_id_str else f'!{from_id:08x}'
            original_message = message_text.strip()  # Keep original case for history
            
            self.logger.info(f"Received message from {sender_id} {'(broadcast)' if is_broadcast else 'to us'}: {message_text_lower}")
            
            # Handle new user greetings for broadcast messages
            if is_broadcast:
                self._handle_new_user_greeting(from_id, sender_id)
            
            # Process keywords only if message is directed to us
            response = None
            if is_for_us:
                response = self._process_keywords(message_text_lower, sender_id)
            
            # Log to history file with all details (only if directed to us or greeting sent)
            if is_for_us or (is_broadcast and self._should_greet_user(from_id)):
                self._log_to_history("received", sender_id, original_message, response)
            
        except Exception as e:
            # Handle protobuf decode errors and other message processing issues
            if "DecodeError" in str(type(e)) or "protobuf" in str(e).lower():
                # Enhanced protobuf error handling for radio interference/corrupted packets
                self.logger.debug(f"Protobuf decode error details - Type: {type(e)}, Message: {e}")
                self.logger.warning(f"Meshtastic protobuf decode error (likely radio interference/corrupted packet)")
                
                # Track protobuf errors for restart mechanism
                self._track_protobuf_error()
                
                self.logger.info("This is normal with poor radio conditions - continuing operation...")
                # Don't re-raise, just continue - these errors are expected with radio interference
            else:
                self.logger.error(f"Error processing received message: {e}")
                self.logger.error(f"Traceback available in debug mode. Error: {e}")
    
    def _process_keywords(self, message: str, sender_id: str) -> Optional[str]:
        """
        Process message text for configured keywords and generate responses
        
        Args:
            message: Lowercase message text to scan for keywords
            sender_id: Node ID of the message sender (for response targeting)
            
        Returns:
            Response string if keyword found and processed, None otherwise
            
        Logic:
        1. Get all configured keywords from config
        2. Check if any keyword appears in the message text
        3. For first matching keyword, lookup MQTT topic data
        4. Format and send response to original sender
        5. Return response text for history logging
        """
        keywords = self.config.get_keywords()
        self.logger.debug(f"Keyword Check - Message: '{message}', Available keywords: {list(keywords.keys())}")
        
        # Process keywords in config order, respond to first match only
        for keyword, topic in keywords.items():
            # Check for #keyword pattern in the message
            keyword_pattern = f"#{keyword.lower()}"
            if keyword_pattern in message:
                self.logger.info(f"Keyword pattern '{keyword_pattern}' detected in message")
                self.logger.debug(f"Keyword Match - Pattern: '{keyword_pattern}', Topic: '{topic}', Looking up MQTT data...")
                
                # Retrieve cached MQTT data for this keyword's topic with retry logic
                mqtt_data = self.mqtt_manager.get_topic_data(topic)
                
                if mqtt_data:
                    response = f"{keyword.title()}: {mqtt_data}"
                    self.logger.debug(f"MQTT Data Found - Keyword: '{keyword}', Topic: '{topic}', Data length: {len(mqtt_data)} chars")
                else:
                    # Attempt to refresh topic data up to 3 times before giving up
                    if self.mqtt_manager.connected:
                        self.logger.info(f"MQTT data cache expired for '{topic}', attempting refresh (up to 3 tries)...")
                        
                        mqtt_data = None
                        for attempt in range(1, 4):  # 3 attempts
                            self.logger.debug(f"MQTT refresh attempt {attempt}/3 for topic '{topic}'")
                            
                            # Trigger topic refresh
                            self.mqtt_manager._check_and_refresh_topic(topic)
                            
                            # Wait a bit for new data to arrive
                            time.sleep(2)
                            
                            # Check if we got fresh data
                            mqtt_data = self.mqtt_manager.get_topic_data(topic)
                            if mqtt_data:
                                self.logger.info(f"MQTT data refresh successful on attempt {attempt} for topic '{topic}'")
                                break
                            
                            self.logger.debug(f"MQTT refresh attempt {attempt} failed for topic '{topic}', no fresh data received")
                        
                        if mqtt_data:
                            response = f"{keyword.title()}: {mqtt_data}"
                            self.logger.debug(f"MQTT Data Found After Refresh - Keyword: '{keyword}', Topic: '{topic}', Data length: {len(mqtt_data)} chars")
                        else:
                            response = f"{keyword.title()}: No recent data available (cache expired after 3 refresh attempts)"
                            self.logger.warning(f"MQTT Data Missing - Keyword: '{keyword}', Topic: '{topic}', Cache expired and no fresh data received after 3 refresh attempts")
                    else:
                        response = f"{keyword.title()}: Connection unavailable"
                        self.logger.warning(f"MQTT Data Missing - Keyword: '{keyword}', Topic: '{topic}', MQTT broker connection lost")
                
                # Send response back to the original message sender
                self._send_response(response, sender_id)
                return response  # Return response for history logging
        
        return None  # No keyword matched
    
    def _send_response(self, message: str, destination_id: str):
        """
        Send response message back to the original sender
        
        Args:
            message: Response text to send
            destination_id: Node ID of recipient (hex string format like '!5691465b')
            
        Process:
        1. Convert destination ID from string to integer format
        2. Split message into chunks accounting for multi-part prefixes
        3. Send messages using Meshtastic interface with delays between chunks
        4. Log successful transmission
        5. Handle and log any transmission errors
        """
        try:
            # Convert destination_id from string format back to int if needed
            if isinstance(destination_id, str) and destination_id.startswith('!'):
                dest_id = int(destination_id[1:], 16)
            elif destination_id == '^all':
                # Use broadcast node ID for mesh-wide broadcasts
                dest_id = 0xFFFFFFFF  # Meshtastic broadcast node ID
            else:
                dest_id = destination_id
            
            # First, determine if we need multi-part messages and calculate prefix length
            # Estimate how many parts we'll need for proper prefix calculation
            estimated_parts = (len(message) + 149) // 150  # Round up division
            if estimated_parts > 1:
                # Calculate prefix length: "(X/Y) " where X and Y are the part numbers
                prefix_len = len(f"({estimated_parts}/{estimated_parts}) ")
                max_chunk_size = 150 - prefix_len
            else:
                max_chunk_size = 150
            
            # Split message into properly sized chunks accounting for prefixes
            chunks = []
            remaining = message
            while len(remaining) > max_chunk_size:
                chunks.append(remaining[:max_chunk_size])
                remaining = remaining[max_chunk_size:]
            if remaining:
                chunks.append(remaining)
            
            # Send each chunk with appropriate prefix
            for i, chunk in enumerate(chunks):
                if len(chunks) > 1:
                    prefix = f"({i+1}/{len(chunks)}) "
                    final_message = prefix + chunk
                else:
                    final_message = chunk
                
                self.logger.info(f"Sending response to {destination_id} (ID: {dest_id}): {final_message}")
                self.interface.sendText(final_message, destinationId=dest_id)
                self.logger.info(f"Sent response to {destination_id}: {final_message}")
                
                # Add delay between messages to avoid overwhelming the mesh
                if i < len(chunks) - 1:
                    time.sleep(5)
                    
        except Exception as e:
            self.logger.error(f"Failed to send response: {e}")
    
    def _handle_mqtt_message_request(self, mac_address: str, message: str):
        """
        Handle message sending request from MQTT
        
        Args:
            mac_address: MAC address in format XX:XX:XX:XX:XX:XX
            message: Message text to send
        """
        try:
            # Convert MAC address to node ID
            node_id = self._mac_to_node_id(mac_address)
            
            self.logger.info(f"Sending MQTT-requested message to MAC {mac_address} (Node: {node_id}): {message}")
            
            # Send the message using existing response method
            self._send_response(message, node_id)
            
            # Log to history
            self._log_to_history("mqtt_send", node_id, f"MQTT Request: {mac_address}@{message}", message)
            
        except Exception as e:
            self.logger.error(f"Failed to send MQTT-requested message to {mac_address}: {e}")
    
    def _track_protobuf_error(self):
        """
        Track protobuf parsing errors and request restart if threshold exceeded
        
        Implements a sliding window error counter:
        - Tracks errors in 5-minute windows
        - Resets window when time expires
        - Requests restart when max errors per window exceeded
        - Logs error statistics for monitoring
        
        This helps recover from persistent radio interference or device issues
        that cause continuous protobuf parsing failures.
        """
        current_time = time.time()
        
        # Check if we need to reset the error window
        if current_time - self.error_window_start > self.error_window_duration:
            if self.protobuf_error_count > 0:
                self.logger.info(f"Protobuf error window reset - Had {self.protobuf_error_count} errors in last {self.error_window_duration/60:.1f} minutes")
            self.protobuf_error_count = 0
            self.error_window_start = current_time
        
        # Increment error count
        self.protobuf_error_count += 1
        
        # Log error statistics
        window_elapsed = current_time - self.error_window_start
        self.logger.debug(f"Protobuf error tracking - Count: {self.protobuf_error_count}/{self.max_errors_per_window}, Window: {window_elapsed/60:.1f}/{self.error_window_duration/60:.1f} minutes")
        
        # Check if we've exceeded the error threshold
        if self.protobuf_error_count >= self.max_errors_per_window:
            self.logger.error(f"Protobuf error threshold exceeded: {self.protobuf_error_count} errors in {window_elapsed/60:.1f} minutes")
            self.logger.error("This suggests persistent radio interference or device issues")
            self.logger.error("Requesting daemon restart to recover from potential stuck state...")
            
            # Set restart flag and stop monitoring
            self.restart_requested = True
            self.stop_monitoring()
    
    def _handle_new_user_greeting(self, from_id: int, sender_id: str):
        """
        Handle greeting new users who send broadcast messages
        
        Uses configurable greeting format from daemon configuration.
        Available format variables:
        - {node_id}: The sender's node ID (e.g., '!12345678')
        - {node_id_short}: Short version without '!' prefix (e.g., '12345678')
        - {bot_id}: This bot's node ID
        
        Args:
            from_id: Numeric node ID of the sender
            sender_id: String representation of sender ID (e.g., '!12345678')
        """
        try:
            # Check if greeting is enabled
            greeting_enabled = self.config.get('daemon', 'greeting_enabled', 'true').lower() == 'true'
            if not greeting_enabled:
                return
                
            # Clean up expired entries from cache first
            self._clean_greeting_cache()
            
            # Check if we should greet this user
            if self._should_greet_user(from_id):
                # Get configurable greeting format
                greeting_format = self.config.get('daemon', 'greeting_format', 'Hello {node_id}! Welcome to the mesh network!')
                
                # Prepare format variables
                bot_hex_id = f'!{self.my_node_id:08x}' if self.my_node_id else '!unknown'
                sender_short = sender_id.lstrip('!') if sender_id.startswith('!') else sender_id
                
                # Format the greeting message
                greeting = greeting_format.format(
                    node_id=sender_id,
                    node_id_short=sender_short,
                    bot_id=bot_hex_id
                )
                
                self.logger.info(f"Greeting new user: {sender_id}")
                
                # Send greeting as broadcast so others can see it too
                self._send_response(greeting, '^all')
                
                # Add to greeted users cache
                current_time = time.time()
                self.greeted_users[from_id] = current_time
                
                # Log greeting to history
                self._log_to_history("greeting", sender_id, f"New user detected on broadcast", greeting)
                
        except Exception as e:
            self.logger.error(f"Failed to handle new user greeting for {sender_id}: {e}")
    
    def _should_greet_user(self, from_id: int) -> bool:
        """
        Check if we should greet a user (not already greeted within cache duration)
        
        Args:
            from_id: Numeric node ID to check
            
        Returns:
            bool: True if user should be greeted, False if already greeted recently
        """
        current_time = time.time()
        
        # Check if user is in cache
        if from_id not in self.greeted_users:
            return True
        
        # Check if cache entry has expired
        last_greeting_time = self.greeted_users[from_id]
        time_since_greeting = current_time - last_greeting_time
        
        if time_since_greeting >= self.greeting_cache_duration:
            # Cache expired, user can be greeted again
            return True
        
        # User was greeted recently, don't greet again
        self.logger.debug(f"User {from_id:08x} was greeted {time_since_greeting:.0f}s ago, skipping greeting")
        return False
    
    def _clean_greeting_cache(self):
        """
        Remove expired entries from the greeting cache to prevent memory buildup
        """
        current_time = time.time()
        expired_users = []
        
        for user_id, greeting_time in self.greeted_users.items():
            if current_time - greeting_time >= self.greeting_cache_duration:
                expired_users.append(user_id)
        
        for user_id in expired_users:
            del self.greeted_users[user_id]
            
        if expired_users:
            self.logger.debug(f"Cleaned {len(expired_users)} expired greeting cache entries")
    
    def should_restart(self) -> bool:
        """
        Check if a restart has been requested due to protobuf errors
        
        Returns:
            bool: True if restart is needed, False otherwise
        """
        return self.restart_requested


class MeshVMDaemon:
    """
    Main daemon orchestrator - coordinates all components
    
    Responsibilities:
    - Initialize and coordinate all subsystems
    - Handle daemon lifecycle (start/stop/signals)
    - Setup logging and process management
    - Provide clean shutdown procedures
    
    Architecture:
        MeshVMDaemon
        ├── MeshVMConfig (configuration management)
        ├── MQTTManager (MQTT client & topic caching)
        └── MeshtasticMonitor (serial interface & message processing)
        
    Lifecycle:
    1. Initialize configuration and logging
    2. Create PID file for process management
    3. Start MQTT manager and wait for connection
    4. Start Meshtastic monitor and begin message processing
    5. Handle signals for graceful shutdown
    """
    
    def __init__(self, config_path: str = "/etc/meshvm/meshvm.conf", foreground: bool = False):
        """Initialize daemon with configuration file path and foreground mode flag"""
        self.config = MeshVMConfig(config_path)
        self.foreground = foreground
        self.logger = None  # Logger instance (initialized in setup_logging)
        self.mqtt_manager = None  # MQTTManager instance
        self.meshtastic_monitor = None  # MeshtasticMonitor instance
        self.running = False  # Daemon state flag
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def setup_logging(self):
        """
        Setup logging configuration with file output and optional console output
        
        Creates log directory if needed and configures file logging always.
        Console logging is only enabled in foreground mode.
        """
        log_file = os.path.expanduser(self.config.get('daemon', 'log_file'))
        log_level = self.config.get('daemon', 'log_level', 'INFO')
        

        
        # Create log directory if it doesn't exist
        log_dir = Path(log_file).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup handlers based on foreground mode
        handlers = [logging.FileHandler(log_file)]
        if self.foreground:
            handlers.append(logging.StreamHandler(sys.stdout))
        
        # Configure logging
        logging.basicConfig(
            level=getattr(logging, log_level.upper()),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=handlers
        )
        
        self.logger = logging.getLogger('MeshVM')
        self.logger.info("Logging initialized")
    
    def _signal_handler(self, signum, frame):
        """Handle termination signals for graceful shutdown"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
    
    def daemonize(self):
        """
        Daemonize the process by detaching from the terminal
        
        This method performs the standard Unix daemon double-fork to completely
        detach the process from the controlling terminal.
        """
        if self.foreground:
            return  # Skip daemonization in foreground mode
            
        try:
            # First fork
            pid = os.fork()
            if pid > 0:
                sys.exit(0)  # Parent exits
        except OSError as e:
            sys.stderr.write(f"First fork failed: {e}\n")
            sys.exit(1)
        
        # Decouple from parent environment
        os.chdir("/")
        os.setsid()
        os.umask(0)
        
        try:
            # Second fork
            pid = os.fork()
            if pid > 0:
                sys.exit(0)  # Second parent exits
        except OSError as e:
            sys.stderr.write(f"Second fork failed: {e}\n")
            sys.exit(1)
        
        # Redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()
        
        with open(os.devnull, 'r') as devnull_r:
            os.dup2(devnull_r.fileno(), sys.stdin.fileno())
        
        with open(os.devnull, 'w') as devnull_w:
            os.dup2(devnull_w.fileno(), sys.stdout.fileno())
            os.dup2(devnull_w.fileno(), sys.stderr.fileno())
    
    def create_pid_file(self):
        """
        Create PID file for process management
        
        Creates directory if needed and writes current process ID to file.
        This allows system administrators to manage the daemon process.
        """
        pid_file = os.path.expanduser(self.config.get('daemon', 'pid_file'))
        pid_dir = Path(pid_file).parent
        pid_dir.mkdir(parents=True, exist_ok=True)
        
        with open(pid_file, 'w') as f:
            f.write(str(os.getpid()))
        
        self.logger.info(f"PID file created: {pid_file}")
    
    def remove_pid_file(self):
        """Remove PID file during shutdown"""
        pid_file = os.path.expanduser(self.config.get('daemon', 'pid_file'))
        try:
            os.unlink(pid_file)
            self.logger.info("PID file removed")
        except FileNotFoundError:
            pass  # PID file already removed or never created
    
    def start(self):
        """
        Start the daemon with full component initialization
        
        Startup sequence:
        1. Import threading-sensitive libraries (after daemon fork)
        2. Setup logging system
        3. Create PID file for process management
        4. Initialize MQTT manager
        5. Initialize Meshtastic monitor
        6. Connect to MQTT broker (with connection delay)
        7. Connect to Meshtastic device
        8. Start message monitoring loop
        
        Note: Daemonization is now handled in main() before this method is called
        to avoid multi-threading issues with fork().
        
        Raises:
            Exception: If any component fails to initialize or connect
        """
        try:
            # Import threading-sensitive libraries after daemon fork
            _import_threading_libraries()
            
            self.setup_logging()
            self.logger.info(f"Starting MeshVM daemon v{__version__}")
            
            self.create_pid_file()
            
            # Initialize components with dependency injection
            self.mqtt_manager = MQTTManager(self.config, self.logger)
            self.meshtastic_monitor = MeshtasticMonitor(self.config, self.mqtt_manager, self.logger)
            
            # Connect to services in proper order
            self.mqtt_manager.connect()
            time.sleep(2)  # Allow MQTT connection to establish and subscriptions to complete
            
            self.meshtastic_monitor.connect()
            
            self.running = True
            self.logger.info("MeshVM daemon started successfully")
            
            # Start monitoring in main thread with restart handling
            while self.running:
                try:
                    # Start monitoring (blocks until shutdown or restart requested)
                    self.meshtastic_monitor.start_monitoring()
                    
                    # Check if restart was requested due to protobuf errors
                    if self.meshtastic_monitor.should_restart():
                        self.logger.info("Restart requested due to protobuf error threshold - reinitializing daemon")
                        self._restart_daemon()
                    else:
                        # Normal shutdown, exit loop
                        break
                        
                except Exception as e:
                    self.logger.error(f"Error during monitoring: {e}")
                    if self.running:
                        self.logger.info("Attempting to restart after monitoring error...")
                        time.sleep(5)
                        self._restart_daemon()
                    else:
                        break
            
        except Exception as e:
            if hasattr(self, 'logger') and self.logger:
                self.logger.error(f"Failed to start daemon: {e}")
            else:
                sys.stderr.write(f"Failed to start daemon: {e}\n")
            # Don't call self.stop() if we haven't properly initialized
            # Just clean up what we can
            if hasattr(self, 'running'):
                self.running = False
            raise
    
    def _restart_daemon(self):
        """
        Restart daemon components after protobuf error threshold exceeded
        
        Restart process:
        1. Disconnect from Meshtastic device and MQTT broker
        2. Wait for cleanup to complete
        3. Reinitialize components with fresh connections
        4. Reset error tracking state
        5. Resume monitoring
        
        This helps recover from persistent device communication issues
        that cause continuous protobuf parsing failures.
        """
        try:
            self.logger.info("Beginning daemon restart sequence...")
            
            # Disconnect from current connections
            if self.meshtastic_monitor:
                self.meshtastic_monitor.disconnect()
            if self.mqtt_manager:
                self.mqtt_manager.disconnect()
            
            # Wait for cleanup
            time.sleep(3)
            
            # Reinitialize components
            self.logger.info("Reinitializing daemon components...")
            self.mqtt_manager = MQTTManager(self.config, self.logger)
            self.meshtastic_monitor = MeshtasticMonitor(self.config, self.mqtt_manager, self.logger)
            
            # Reconnect services
            self.mqtt_manager.connect()
            time.sleep(2)
            self.meshtastic_monitor.connect()
            
            self.logger.info("Daemon restart completed successfully")
            
        except Exception as e:
            self.logger.error(f"Error during daemon restart: {e}")
            self.logger.error("Restart failed - daemon will attempt normal shutdown")
            self.running = False
    
    def stop(self):
        """
        Stop the daemon with graceful shutdown of all components
        
        Shutdown sequence:
        1. Stop message monitoring
        2. Disconnect from Meshtastic device
        3. Disconnect from MQTT broker
        4. Remove PID file
        5. Log shutdown completion
        """
        if self.running:
            if hasattr(self, 'logger') and self.logger:
                self.logger.info("Stopping MeshVM daemon")
            self.running = False
            
            # Stop monitoring first to prevent new messages
            if self.meshtastic_monitor:
                self.meshtastic_monitor.stop_monitoring()
                self.meshtastic_monitor.disconnect()
            
            # Disconnect from MQTT broker
            if self.mqtt_manager:
                self.mqtt_manager.disconnect()
            
            self.remove_pid_file()
            if hasattr(self, 'logger') and self.logger:
                self.logger.info("MeshVM daemon stopped")


def main():
    """
    Main entry point - handles command line arguments and daemon startup
    
    Command line options:
        --config, -c: Path to configuration file
        --create-config: Generate sample configuration and exit
        --foreground, -f: Run in foreground (don't daemonize)
        --version, -v: Show version and exit
        
    Startup process:
    1. Parse command line arguments
    2. Handle special modes (config creation, version)
    3. Validate configuration file exists and is valid
    4. Daemonize early if not in foreground mode (before any threads are created)
    5. Initialize and start daemon
    6. Handle any startup errors gracefully
    """
    import argparse
    
    # Setup command line argument parser
    parser = argparse.ArgumentParser(description=f'MeshVM - Meshtastic Virtual Machine Daemon v{__version__}')
    parser.add_argument('--config', '-c', default='/etc/meshvm/meshvm.conf',
                       help='Configuration file path')
    parser.add_argument('--create-config', action='store_true',
                       help='Create sample configuration file and exit')
    parser.add_argument('--foreground', '-f', action='store_true',
                       help='Run in foreground (don\'t daemonize)')
    parser.add_argument('--version', '-v', action='version', version=f'MeshVM v{__version__}')
    
    args = parser.parse_args()
    
    # Convert config path to absolute path before daemonizing
    # This ensures the path remains valid after changing working directory to /
    config_path = os.path.abspath(args.config)
    
    # Handle configuration file creation mode
    if args.create_config:
        config = MeshVMConfig(config_path)
        config.create_sample_config()
        print(f"Sample configuration created at: {config_path}")
        print("Please edit the configuration file and set your node_id before running the daemon.")
        return 0
    
    # Validate configuration file exists and has required settings
    config = MeshVMConfig(config_path)
    if not config.get('meshtastic', 'node_id'):
        print("Error: node_id must be configured in the configuration file")
        print(f"Run with --create-config to create a sample configuration at {args.config}")
        return 1
    
    # Daemonize early before creating any objects that might spawn threads
    # This prevents the "multi-threaded process fork()" warning
    if not args.foreground:
        try:
            # First fork
            pid = os.fork()
            if pid > 0:
                sys.exit(0)  # Parent exits
        except OSError as e:
            sys.stderr.write(f"First fork failed: {e}\n")
            sys.exit(1)
        
        # Decouple from parent environment
        os.chdir("/")
        os.setsid()
        os.umask(0)
        
        try:
            # Second fork
            pid = os.fork()
            if pid > 0:
                sys.exit(0)  # Second parent exits
        except OSError as e:
            sys.stderr.write(f"Second fork failed: {e}\n")
            sys.exit(1)
        
        # Redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()
        
        with open(os.devnull, 'r') as devnull_r:
            os.dup2(devnull_r.fileno(), sys.stdin.fileno())
        
        with open(os.devnull, 'w') as devnull_w:
            os.dup2(devnull_w.fileno(), sys.stdout.fileno())
            os.dup2(devnull_w.fileno(), sys.stderr.fileno())
    
    # Now create the daemon object after daemonization is complete
    daemon = MeshVMDaemon(config_path, foreground=args.foreground)
    
    try:
        daemon.start()
    except KeyboardInterrupt:
        daemon.stop()
    except Exception as e:
        if args.foreground:
            print(f"Daemon failed: {e}")
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
