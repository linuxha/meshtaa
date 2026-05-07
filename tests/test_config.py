# Test Configuration Validation and Management

import pytest
import tempfile
import os
from unittest.mock import patch

from meshtaa import MeshtaaConfig


class TestMeshtaaConfigValidation:
    """Test configuration validation with fail-fast behavior"""
    
    def test_valid_config_passes_validation(self, temp_config_file):
        """Test that a valid configuration passes all validation checks"""
        with patch('os.path.exists', return_value=True):  # Mock serial port exists
            config = MeshtaaConfig(temp_config_file)
            assert config.get('meshtastic', 'node_id') == '!12345678'
            assert config.get('mqtt', 'broker') == 'localhost'
    
    def test_missing_node_id_raises_error(self):
        """Test that missing node_id fails validation"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write("""[meshtastic]
connection_type = serial

[mqtt]  
broker = localhost

[keywords]
weather = sensors/weather
""")
            f.flush()
            
        with pytest.raises(ValueError, match="Required field 'node_id' is missing"):
            with patch('os.path.exists', return_value=True):
                MeshtaaConfig(f.name)
        
        os.unlink(f.name)
    
    def test_invalid_connection_type_raises_error(self):
        """Test that invalid connection type fails validation"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write("""[meshtastic]
connection_type = invalid_type
node_id = !12345678

[mqtt]
broker = localhost

[keywords]  
weather = sensors/weather
""")
            f.flush()
            
        with pytest.raises(ValueError, match="Invalid connection_type 'invalid_type'"):
            with patch('os.path.exists', return_value=True):
                MeshtaaConfig(f.name)
        
        os.unlink(f.name)
    
    def test_serial_connection_missing_port_raises_error(self):
        """Test that serial connection without existing port fails validation"""  
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write("""[meshtastic]
connection_type = serial
serial_port = /nonexistent/port
node_id = !12345678

[mqtt]
broker = localhost

[keywords]
weather = sensors/weather
""")
            f.flush()
            
        with pytest.raises(ValueError, match="Serial port '/nonexistent/port' does not exist"):
            MeshtaaConfig(f.name)
        
        os.unlink(f.name)
    
    def test_network_connection_missing_url_raises_error(self):
        """Test that network connection without URL fails validation"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write("""[meshtastic]
connection_type = network  
node_id = !12345678

[mqtt]
broker = localhost

[keywords]
weather = sensors/weather
""")
            f.flush()
            
        with pytest.raises(ValueError, match="Required field 'network_url' is missing"):
            MeshtaaConfig(f.name)
        
        os.unlink(f.name)
    
    def test_network_connection_invalid_url_raises_error(self):
        """Test that invalid network URL fails validation"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write("""[meshtastic]
connection_type = network
network_url = invalid-url
node_id = !12345678

[mqtt]
broker = localhost

[keywords]
weather = sensors/weather  
""")
            f.flush()
            
        with pytest.raises(ValueError, match="Invalid network_url.*Must start with http"):
            MeshtaaConfig(f.name)
        
        os.unlink(f.name)
    
    def test_bluetooth_connection_missing_mac_raises_error(self):
        """Test that Bluetooth connection without MAC fails validation"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write("""[meshtastic]
connection_type = bluetooth
node_id = !12345678

[mqtt]
broker = localhost

[keywords]
weather = sensors/weather
""")
            f.flush()
            
        with pytest.raises(ValueError, match="Required field 'bluetooth_mac' is missing"):
            MeshtaaConfig(f.name)
        
        os.unlink(f.name)
    
    def test_bluetooth_connection_invalid_mac_raises_error(self):
        """Test that invalid Bluetooth MAC address fails validation"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write("""[meshtastic]
connection_type = bluetooth
bluetooth_mac = invalid-mac
node_id = !12345678

[mqtt]
broker = localhost

[keywords]
weather = sensors/weather
""")
            f.flush()
            
        with pytest.raises(ValueError, match="Invalid bluetooth_mac.*Must be in format"):
            MeshtaaConfig(f.name)
        
        os.unlink(f.name)
    
    def test_missing_keywords_raises_error(self):
        """Test that configuration with all keywords removed fails validation"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write("""[meshtastic]
connection_type = serial
node_id = !12345678

[mqtt]
broker = localhost

[keywords]
""")
            f.flush()
        
        with patch('os.path.exists', return_value=True):
            config = MeshtaaConfig(f.name)
        
        # Remove all keywords from the already-loaded config
        config.config.remove_section('keywords')
        config.config.add_section('keywords')  # Empty keywords section
        
        with pytest.raises(ValueError, match="At least one keyword must be configured"):
            config.validate_config()
        
        os.unlink(f.name)
    
    def test_missing_mqtt_broker_raises_error(self):
        """Test that configuration with empty MQTT broker fails validation"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write("""[meshtastic]
connection_type = serial
node_id = !12345678

[mqtt]
port = 1883

[keywords]
weather = sensors/weather
""")
            f.flush()
        
        with patch('os.path.exists', return_value=True):
            config = MeshtaaConfig(f.name)
        
        # Override broker to empty string
        config.config.set('mqtt', 'broker', '')
        
        with pytest.raises(ValueError, match="Required field 'broker' is missing"):
            config.validate_config()
        
        os.unlink(f.name)
    
    def test_invalid_mqtt_port_raises_error(self):
        """Test that invalid MQTT port fails validation"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write("""[meshtastic]
connection_type = serial  
node_id = !12345678

[mqtt]
broker = localhost
port = 99999

[keywords]
weather = sensors/weather
""")
            f.flush()
            
        with pytest.raises(ValueError, match="Invalid MQTT port '99999'"):
            with patch('os.path.exists', return_value=True):
                MeshtaaConfig(f.name)
        
        os.unlink(f.name)
    
    def test_invalid_filter_mode_raises_error(self):
        """Test that invalid filter mode fails validation"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write("""[meshtastic]
connection_type = serial
node_id = !12345678

[mqtt]
broker = localhost

[daemon]
filter_mode = invalid_mode

[keywords]
weather = sensors/weather
""")
            f.flush()
            
        with pytest.raises(ValueError, match="Invalid filter_mode 'invalid_mode'"):
            with patch('os.path.exists', return_value=True):
                MeshtaaConfig(f.name)
        
        os.unlink(f.name)


class TestMeshtaaConfigFunctionality:
    """Test basic configuration functionality"""
    
    def test_get_keywords_returns_dict(self, test_config):
        """Test that get_keywords returns proper dictionary"""
        keywords = test_config.get_keywords()
        assert isinstance(keywords, dict)
        assert 'weather' in keywords
        assert keywords['weather'] == 'sensors/weather'
    
    def test_get_with_fallback(self, test_config):
        """Test get method with fallback values"""
        # Existing value
        assert test_config.get('mqtt', 'broker') == 'localhost'
        
        # Non-existent value with fallback
        assert test_config.get('nonexistent', 'key', 'fallback') == 'fallback'
    
    def test_getint_with_fallback(self, test_config):
        """Test getint method with fallback values"""
        # Existing integer value
        assert test_config.getint('mqtt', 'port') == 1883
        
        # Non-existent value with fallback
        assert test_config.getint('nonexistent', 'key', 123) == 123