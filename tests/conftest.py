# Meshtaa Test Configuration and Fixtures

import pytest
import tempfile
import os
import json
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path

# Import the main module
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from meshtaa import MeshtaaConfig, MQTTManager, MeshtasticMonitor, MeshtaaDaemon
import meshtaa as _meshtaa_module


@pytest.fixture(scope="session", autouse=True)
def import_threading_libraries():
    """Call _import_threading_libraries() once per test session so module globals are populated"""
    _meshtaa_module._import_threading_libraries()


@pytest.fixture
def temp_config_file():
    """Create a temporary config file for testing"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        config_content = """[meshtastic]
connection_type = serial
serial_port = /dev/ttyUSB0
node_id = !12345678

[mqtt]
broker = localhost
port = 1883

[daemon]
log_file = /tmp/test_meshtaa.log
log_level = INFO

[keywords]
weather = sensors/weather
status = system/status
"""
        f.write(config_content)
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture  
def mock_serial_interface():
    """Mock Meshtastic SerialInterface for testing without hardware"""
    with patch('meshtaa.SerialInterface') as mock_interface:
        # Create mock instance
        interface = Mock()
        interface.localNode = Mock()
        interface.localNode.nodeNum = 0x12345678
        interface.nodesByNum = {0x12345678: Mock(user=Mock(id="!12345678", longName="Test Node"))}
        
        # Mock message publishing
        interface.sendText = Mock()
        
        # Mock connection
        interface.close = Mock()
        
        mock_interface.return_value = interface
        yield interface


@pytest.fixture
def mock_mqtt_client():
    """Mock paho-mqtt client for testing without broker"""
    with patch('paho.mqtt.client.Client') as mock_client_class:
        client = Mock()
        
        # Mock connection methods
        client.connect = Mock(return_value=0)  # Success
        client.disconnect = Mock() 
        client.loop_start = Mock()
        client.loop_stop = Mock()
        client.subscribe = Mock()
        client.publish = Mock()
        client.username_pw_set = Mock()
        
        # Mock callback properties
        client.on_connect = None
        client.on_disconnect = None  
        client.on_message = None
        
        mock_client_class.return_value = client
        yield client


@pytest.fixture
def sample_meshtastic_packet():
    """Generate realistic Meshtastic message packet for testing"""
    return {
        'from': 0x87654321,
        'to': 0x12345678,
        'id': 123456789,
        'channel': 0,
        'decoded': {
            'portnum': 'TEXT_MESSAGE_APP',
            'payload': b'weather',
            'text': 'weather'
        },
        'fromId': '!87654321',
        'toId': '!12345678'
    }


@pytest.fixture 
def sample_broadcast_packet():
    """Generate realistic broadcast message packet for testing"""
    return {
        'from': 0x87654321,
        'to': 0xffffffff,  # Broadcast address
        'id': 123456790,
        'channel': 0,
        'decoded': {
            'portnum': 'TEXT_MESSAGE_APP', 
            'payload': b'Hello mesh!',
            'text': 'Hello mesh!'
        },
        'fromId': '!87654321',
        'toId': '!ffffffff'
    }


@pytest.fixture
def mock_logger():
    """Mock logger for testing without log files"""
    logger = Mock()
    logger.info = Mock()
    logger.debug = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    logger.exception = Mock()
    return logger


@pytest.fixture  
def test_config():
    """Create test configuration instance with valid settings"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        config_content = """[meshtastic]
connection_type = serial
serial_port = /dev/ttyUSB0
node_id = !12345678

[mqtt]
broker = localhost
port = 1883

[daemon]
log_file = /tmp/test_meshtaa.log
log_level = INFO
filter_mode = none

[keywords]
weather = sensors/weather
status = system/status
test = test/topic
"""
        f.write(config_content)
        f.flush()
        
        # Mock the serial port existence check for validation
        with patch('os.path.exists', return_value=True):
            config = MeshtaaConfig(f.name)
        
    os.unlink(f.name)
    return config


@pytest.fixture
def sample_mqtt_message():
    """Sample MQTT message for testing caching"""
    return {
        'topic': 'sensors/weather',
        'payload': '{"temperature": 23.5, "humidity": 65, "status": "sunny"}',
        'timestamp': 1234567890.0
    }


# Test utilities
def create_chunked_message_test_cases():
    """Generate test cases for message chunking logic"""
    return [
        # (message, expected_chunks)
        ("Short", ["Short"]),  # Single chunk
        ("A" * 150, ["A" * 150]),  # Exactly max size  
        ("A" * 151, ["(1/2) " + "A" * 144, "(2/2) " + "A" * 7]),  # Two chunks with prefix
        ("A" * 300, ["(1/3) " + "A" * 144, "(2/3) " + "A" * 144, "(3/3) " + "A" * 12]),  # Three chunks
    ]


def mock_time_freeze(frozen_time):
    """Context manager to freeze time for testing cache expiration"""
    with patch('time.time', return_value=frozen_time):
        yield