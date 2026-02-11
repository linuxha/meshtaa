#!/usr/bin/env python3
"""
MeshVM - Meshtastic Virtual Machine Daemon

A Linux daemon that monitors Meshtastic messages via serial port and responds with MQTT data.
The daemon:
- Connects to a Meshtastic device via serial port
- Monitors incoming TEXT messages directed to this node
- Processes messages for configurable keywords
- Retrieves cached data from MQTT topics
- Sends responses back to the original message sender

Architecture:
    Meshtastic Device <--> SerialInterface <--> MeshtasticMonitor
                                                        |
    MQTT Broker <--> MQTTManager <--> MeshVMDaemon <----+
                                           |
                                    Configuration

Author: Senior Software Engineer
Date: February 2026
Version: 0.5.0
"""

__version__ = "0.8.1"

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

try:
    import configparser
    import logging.handlers
    import paho.mqtt.client as mqtt
    import ssl
    import urllib3
    from urllib.parse import urlparse

    from meshtastic.serial_interface import SerialInterface
    from meshtastic.tcp_interface import TCPInterface
    from meshtastic.ble_interface import BLEInterface
    from meshtastic import mesh_pb2
    from meshtastic.protobuf import portnums_pb2
    
    # Disable SSL warnings for certificate verification bypass
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError as e:
    print(f"Required dependency missing: {e}")
    print("Install with: pip install pyserial paho-mqtt meshtastic")
    sys.exit(1)
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
        self.config.set('meshtastic', 'bluetooth_mac', '')  # Bluetooth MAC address (e.g., 01:23:45:67:89:AB)
        self.config.set('meshtastic', 'bluetooth_pin', '')  # Optional Bluetooth PIN if required
        self.config.set('meshtastic', 'node_id', '')  # Must be set by user
        
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
        self.client = mqtt.Client()  # Create MQTT client instance
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
        """Connect to MQTT broker and start message loop"""
        try:
            broker = self.config.get('mqtt', 'broker')
            port = self.config.getint('mqtt', 'port', 1883)
            keepalive = self.config.getint('mqtt', 'keepalive', 60)
            
            self.logger.info(f"Connecting to MQTT broker {broker}:{port}")
            self.client.connect(broker, port, keepalive)
            self.client.loop_start()
        except Exception as e:
            self.logger.error(f"Failed to connect to MQTT broker: {e}")
            raise
    
    def disconnect(self):
        """Disconnect from MQTT broker and stop message loop"""
        if self.connected:
            self.client.loop_stop()
            self.client.disconnect()
    
    def _on_connect(self, client, userdata, flags, rc):
        """
        MQTT connection callback - handles successful connections and subscriptions
        
        Args:
            client: MQTT client instance
            userdata: User data (unused)
            flags: Connection flags
            rc: Connection result code (0 = success)
        """
        broker = self.config.get('mqtt', 'broker')
        port = self.config.getint('mqtt', 'port', 1883)
        
        if rc == 0:
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
            self.logger.error(f"Failed to connect to MQTT broker {broker}:{port}, return code {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """
        MQTT disconnection callback - handles connection loss
        
        Args:
            client: MQTT client instance
            userdata: User data (unused) 
            rc: Disconnection result code
        """
        self.connected = False
        self.logger.warning(f"Disconnected from MQTT broker, return code {rc}")
    
    def _on_message(self, client, userdata, msg):
        """
        MQTT message callback - caches incoming messages with timestamps
        
        Args:
            client: MQTT client instance
            userdata: User data (unused)
            msg: MQTT message object with topic and payload
        """
        broker = self.config.get('mqtt', 'broker')
        port = self.config.getint('mqtt', 'port', 1883)
        topic = msg.topic
        payload = msg.payload.decode('utf-8')
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
        
        self.logger.debug(f"MQTT Cache Miss - Server: {broker}:{port}, Topic: '{topic}', Cache size: {len(self.topic_cache)}")
        return None
    
    def set_message_callback(self, callback):
        """Set callback function for handling message sending requests"""
        self.message_callback = callback
    
    def _handle_message_request(self, payload: str):
        """
        Handle message sending request from MQTT topic
        
        Expected format: <MAC_ADDRESS>@<MESSAGE>
        Example: 10:20:BA:75:9C:D8@Hi there
        
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
    
    def connect(self):
        """
        Connect to Meshtastic device via serial, network, or Bluetooth interface
        
        Connection process:
        1. Connect to device using serial port, network URL, or Bluetooth MAC
        2. Setup chat history logging
        3. Determine this node's ID (from device or config)
        4. Validate node ID is properly configured
        
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
                
                self.logger.info(f"Connecting to Meshtastic device via Bluetooth: {bluetooth_mac}")
                if bluetooth_pin:
                    self.logger.info("Bluetooth PIN provided for authentication")
                else:
                    self.logger.info("No Bluetooth PIN provided - attempting connection without PIN")
                
                # Create BLE interface with optional PIN
                if bluetooth_pin:
                    # Note: PIN handling may vary based on meshtastic library implementation
                    # Some versions might require PIN during pairing, not during connection
                    self.logger.info(f"Using Bluetooth PIN: {bluetooth_pin[:3]}***")
                    self.interface = BLEInterface(address=bluetooth_mac)
                else:
                    self.interface = BLEInterface(address=bluetooth_mac)
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
        3. Check if message is directed to this node
        4. Verify message is TEXT_MESSAGE_APP type
        5. Process message for keywords if all checks pass
        6. Log interaction to history file
        
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
            
            # Check if message is for us (compare numeric IDs)
            if to_id != self.my_node_id:
                # Also check string format as fallback
                expected_id_str = f'!{self.my_node_id:08x}'
                if to_id_str != expected_id_str:
                    return  # Message not for us
            
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
            
            self.logger.info(f"Received message from {sender_id} to us: {message_text_lower}")
            
            # Process keywords and get response
            response = self._process_keywords(message_text_lower, sender_id)
            
            # Log to history file with all details
            self._log_to_history("received", sender_id, original_message, response)
            
        except Exception as e:
            # Handle protobuf decode errors and other message processing issues
            if "DecodeError" in str(type(e)) or "protobuf" in str(e).lower():
                # Enhanced protobuf error handling for radio interference/corrupted packets
                self.logger.debug(f"Protobuf decode error details - Type: {type(e)}, Message: {e}")
                self.logger.warning(f"Meshtastic protobuf decode error (likely radio interference/corrupted packet)")
                self.logger.info("This is normal with poor radio conditions - continuing operation...")
                # Don't re-raise, just continue - these errors are expected with radio interference
            else:
                self.logger.error(f"Error processing received message: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
    
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
            if keyword.lower() in message:
                self.logger.info(f"Keyword '{keyword}' detected in message")
                self.logger.debug(f"Keyword Match - Keyword: '{keyword}', Topic: '{topic}', Looking up MQTT data...")
                
                # Retrieve cached MQTT data for this keyword's topic
                mqtt_data = self.mqtt_manager.get_topic_data(topic)
                
                if mqtt_data:
                    response = f"{keyword.title()}: {mqtt_data}"
                    self.logger.debug(f"MQTT Data Found - Keyword: '{keyword}', Topic: '{topic}', Data length: {len(mqtt_data)} chars")
                else:
                    response = f"{keyword.title()}: No data available"
                    self.logger.warning(f"MQTT Data Missing - Keyword: '{keyword}', Topic: '{topic}', Check MQTT broker and topic subscription")
                
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
        1. Setup logging system
        2. Create PID file for process management
        3. Initialize MQTT manager
        4. Initialize Meshtastic monitor
        5. Connect to MQTT broker (with connection delay)
        6. Connect to Meshtastic device
        7. Start message monitoring loop
        
        Raises:
            Exception: If any component fails to initialize or connect
        """
        try:
            # Daemonize before setting up logging if not in foreground mode
            self.daemonize()
            
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
            
            # Start monitoring in main thread (blocks until shutdown)
            self.meshtastic_monitor.start_monitoring()
            
        except Exception as e:
            self.logger.error(f"Failed to start daemon: {e}")
            self.stop()  # Clean up any partial initialization
            raise
    
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
    4. Initialize and start daemon
    5. Handle any startup errors gracefully
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
    
    # Handle configuration file creation mode
    if args.create_config:
        config = MeshVMConfig(args.config)
        config.create_sample_config()
        print(f"Sample configuration created at: {args.config}")
        print("Please edit the configuration file and set your node_id before running the daemon.")
        return 0
    
    # Validate configuration file exists and has required settings
    config = MeshVMConfig(args.config)
    if not config.get('meshtastic', 'node_id'):
        print("Error: node_id must be configured in the configuration file")
        print(f"Run with --create-config to create a sample configuration at {args.config}")
        return 1
    
    daemon = MeshVMDaemon(args.config, foreground=args.foreground)
    
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
