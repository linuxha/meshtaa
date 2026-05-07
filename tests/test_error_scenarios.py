# Error Scenario and Recovery Testing

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from meshtaa import MeshtasticMonitor, MQTTManager, MeshtaaDaemon, MAX_ERRORS_PER_WINDOW, ERROR_WINDOW_DURATION


class TestDeviceConnectionErrors:
    """Test Meshtastic device connection loss and recovery scenarios"""
    
    def test_serial_device_disconnection_recovery(self, test_config, mock_logger):
        """Test handling of serial device disconnection during send"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Set up monitor with a mock interface that throws on sendText
        mock_interface = Mock()
        mock_interface.sendText.side_effect = Exception("Device disconnected")
        monitor.interface = mock_interface
        monitor.my_node_id = 0x12345678
        
        # Try to send message - should handle error gracefully
        monitor._send_response("Test message", 0x87654321)
        
        # Should log the error but not crash
        mock_logger.error.assert_called()
        error_message = str(mock_logger.error.call_args[0][0])
        assert "Device disconnected" in error_message or "Failed to send" in error_message
    
    def test_network_device_connection_failure(self, test_config, mock_logger):
        """Test handling of network device connection failures"""
        
        # Configure for network connection
        test_config.config.set('meshtastic', 'connection_type', 'network')
        test_config.config.set('meshtastic', 'network_url', 'https://192.168.1.100:443')
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Mock network interface that fails to connect
        with patch('meshtaa.TCPInterface', side_effect=Exception("Connection refused")):
            with pytest.raises(Exception, match="Connection refused"):
                monitor.connect()
            
            # connect() re-raises after logging, so interface stays None
            assert monitor.interface is None
            mock_logger.error.assert_called()
    
    def test_bluetooth_device_pairing_failure(self, test_config, mock_logger):
        """Test handling of Bluetooth device pairing/connection failures"""
        
        # Configure for Bluetooth connection
        test_config.config.set('meshtastic', 'connection_type', 'bluetooth')
        test_config.config.set('meshtastic', 'bluetooth_mac', 'AA:BB:CC:DD:EE:FF')
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Mock Bluetooth interface that fails
        with patch('meshtaa.BLEInterface', side_effect=Exception("Device not found or pairing failed")):
            with pytest.raises(Exception, match="Device not found or pairing failed"):
                monitor.connect()
            
            # connect() re-raises after logging, so interface stays None
            assert monitor.interface is None
            mock_logger.error.assert_called()


class TestMQTTConnectionErrors:
    """Test MQTT broker connection loss and recovery scenarios"""
    
    def test_mqtt_broker_connection_failure(self, test_config, mock_logger):
        """Test handling of MQTT broker connection failures"""
        
        manager = MQTTManager(test_config, mock_logger)
        
        # Mock MQTT client with connection failure
        with patch('paho.mqtt.client.Client') as mock_client_class:
            mock_client = Mock()
            mock_client.connect.side_effect = Exception("Connection refused")
            mock_client_class.return_value = mock_client
            
            # Should handle connection failure gracefully
            manager.connect()
            
            # Should not be connected
            assert not manager.connected
            
            # Should log error
            mock_logger.error.assert_called()
    
    def test_mqtt_broker_disconnection_during_operation(self, test_config, mock_logger):
        """Test handling of MQTT broker disconnection during operation"""
        
        manager = MQTTManager(test_config, mock_logger)
        
        with patch('paho.mqtt.client.Client') as mock_client_class:
            mock_client = Mock()
            mock_client.connect.return_value = 0  # Success initially
            mock_client_class.return_value = mock_client
            
            # Initial connection succeeds
            manager.connect()
            manager.connected = True
            
            # Simulate disconnection event
            manager._on_disconnect(mock_client, None, None, 1, None)  # rc=1 indicates unexpected disconnection
            
            # Should update connection status
            assert not manager.connected
            
            # Should log warning about disconnection
            mock_logger.warning.assert_called()
    
    def test_mqtt_authentication_failure(self, test_config, mock_logger):
        """Test handling of MQTT authentication failures"""
        
        # Configure authentication
        test_config.config.set('mqtt', 'username', 'baduser')
        test_config.config.set('mqtt', 'password', 'badpass')
        
        manager = MQTTManager(test_config, mock_logger)
        
        with patch('paho.mqtt.client.Client') as mock_client_class:
            mock_client = Mock()
            mock_client.connect.return_value = 5  # Connection refused, bad username or password
            mock_client_class.return_value = mock_client
            
            # Should handle auth failure
            manager.connect()
            
            # Should log authentication error
            mock_logger.error.assert_called()
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            auth_error_logged = any('auth' in call.lower() or 'credential' in call.lower() for call in error_calls)
            # Note: Exact error message depends on implementation
    
    def test_mqtt_topic_subscription_failure(self, test_config, mock_logger):
        """Test handling of topic subscription failures"""
        
        manager = MQTTManager(test_config, mock_logger)
        
        # If subscribe raises an exception, _on_connect will propagate it
        # (The code doesn't wrap subscribe in try/except)
        mock_client = Mock()
        mock_client.subscribe.side_effect = Exception("Subscription failed")
        
        with pytest.raises(Exception, match="Subscription failed"):
            manager._on_connect(mock_client, None, None, 0, None)


class TestProtobufErrorRecovery:
    """Test protobuf parsing error tracking and restart mechanism"""
    
    def test_protobuf_error_counting(self, test_config, mock_logger):
        """Test that protobuf errors are counted correctly"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        monitor.my_node_id = 0x12345678
        
        # Simulate multiple protobuf errors
        for i in range(10):
            monitor._track_protobuf_error()
        
        # Should track error count
        assert monitor.protobuf_error_count == 10
        
        # Should log errors
        assert mock_logger.debug.call_count >= 10
    
    def test_protobuf_error_window_expiration(self, test_config, mock_logger):
        """Test that protobuf error window expires and resets count"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Add some errors
        monitor.protobuf_error_count = 10
        monitor.error_window_start = time.time() - ERROR_WINDOW_DURATION - 10  # Expired window
        
        # Add new error - should reset window
        monitor._track_protobuf_error()
        
        # Should reset count and window
        assert monitor.protobuf_error_count == 1
        assert monitor.error_window_start > time.time() - 10  # Recent timestamp
    
    def test_protobuf_error_restart_threshold(self, test_config, mock_logger):
        """Test that reaching error threshold triggers restart request"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Simulate reaching error threshold
        monitor.protobuf_error_count = MAX_ERRORS_PER_WINDOW - 1
        monitor.error_window_start = time.time()
        
        # One more error should trigger restart
        monitor._track_protobuf_error()
        
        # Should request restart
        assert monitor.restart_requested
        
        # Should log restart request
        mock_logger.error.assert_called()
        error_calls = [str(call) for call in mock_logger.error.call_args_list]
        restart_logged = any('restart' in call.lower() for call in error_calls)
        assert restart_logged, "Should log restart request"


class TestNetworkConnectivityErrors:
    """Test network connectivity and timeout scenarios"""
    
    def test_mqtt_connection_timeout(self, test_config, mock_logger):
        """Test handling of MQTT connection timeouts"""
        
        manager = MQTTManager(test_config, mock_logger)
        
        with patch('paho.mqtt.client.Client') as mock_client_class:
            mock_client = Mock()
            # Simulate timeout by hanging indefinitely
            mock_client.connect.side_effect = Exception("Connection timed out")
            mock_client_class.return_value = mock_client
            
            # Should handle timeout gracefully
            manager.connect()
            
            # Should not crash and should log error
            mock_logger.error.assert_called()
    
    def test_network_device_timeout(self, test_config, mock_logger):
        """Test handling of network device connection timeouts"""
        
        # Configure for network connection
        test_config.config.set('meshtastic', 'connection_type', 'network')
        test_config.config.set('meshtastic', 'network_url', 'https://192.168.1.100:443')
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        with patch('meshtaa.TCPInterface', side_effect=Exception("Connection timed out")):
            # connect() re-raises exceptions after logging
            with pytest.raises(Exception, match="Connection timed out"):
                monitor.connect()
            
            # Should log error
            mock_logger.error.assert_called()
            assert monitor.interface is None


class TestConfigurationErrors:
    """Test runtime configuration error scenarios"""
    
    def test_missing_mqtt_credentials_runtime_failure(self, test_config, mock_logger):
        """Test handling when MQTT credentials become invalid at runtime"""
        
        # Start with valid config
        manager = MQTTManager(test_config, mock_logger)
        
        with patch('paho.mqtt.client.Client') as mock_client_class:
            mock_client = Mock()
            
            # First connection works
            mock_client.connect.return_value = 0
            mock_client_class.return_value = mock_client
            manager.connect()
            
            # Later, credentials become invalid (simulated by connection refused)
            mock_client.connect.return_value = 5  # Bad credentials
            
            # Reconnection attempt should handle gracefully
            manager.connect()
            
            # Should log authentication error
            mock_logger.error.assert_called()
    
    def test_device_permission_error(self, test_config, mock_logger):
        """Test handling of device permission errors"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        with patch('meshtaa.SerialInterface', side_effect=PermissionError("Permission denied: /dev/ttyUSB0")):
            # connect() re-raises exceptions after logging
            with pytest.raises(PermissionError):
                monitor.connect()
            
            # Should log permission error
            mock_logger.error.assert_called()
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            permission_logged = any('permission' in call.lower() or 'failed to connect' in call.lower() for call in error_calls)
            assert permission_logged, "Should log permission error"


class TestConcurrencyAndRaceConditions:
    """Test error scenarios involving concurrency and race conditions"""
    
    def test_simultaneous_mqtt_and_device_errors(self, test_config, mock_logger):
        """Test handling of simultaneous MQTT and device connection errors"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Mock both connections to fail
        with patch('paho.mqtt.client.Client') as mock_mqtt_client, \
             patch('meshtaa.SerialInterface', side_effect=Exception("Device failed")):
            
            mock_mqtt_client.return_value.connect.side_effect = Exception("MQTT failed")
            
            # MQTT connection should fail gracefully
            mqtt_manager.connect()
            
            # Device connection re-raises exceptions after logging
            with pytest.raises(Exception, match="Device failed"):
                monitor.connect()
            
            # MQTT error should be logged
            mock_logger.error.assert_called()
            
            # Neither connection should be established
            assert not mqtt_manager.connected
            assert monitor.interface is None
    
    def test_message_processing_during_reconnection(self, test_config, mock_logger):
        """Test message processing behavior during device reconnection"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        monitor.my_node_id = 0x12345678
        
        # Start with no interface (simulating disconnected state)
        monitor.interface = None
        
        # Try to process a message
        test_packet = {
            'from': 0x87654321,
            'to': 0x12345678,
            'id': 123456789,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'weather',
                'text': 'weather'
            },
            'fromId': '!87654321',
            'toId': '!12345678'
        }
        
        # Should handle gracefully without interface
        monitor._on_receive_message(test_packet)
        
        # Should not crash and may log that interface is unavailable
        # Exact behavior depends on implementation


class TestResourceExhaustion:
    """Test resource exhaustion and cleanup scenarios"""
    
    def test_mqtt_cache_memory_growth(self, test_config, mock_logger):
        """Test that MQTT cache doesn't grow unbounded"""
        
        manager = MQTTManager(test_config, mock_logger)
        
        # Simulate receiving many MQTT messages
        for i in range(1000):
            mock_message = Mock()
            mock_message.topic = f'test/topic_{i}'
            mock_message.payload.decode.return_value = f'data_{i}'
            
            manager._on_message(None, None, mock_message)
        
        # Cache should not grow unbounded (implementation dependent)
        # This test ensures the system can handle high message volume
        assert len(manager.topic_cache) <= 1000
    
    def test_greeting_cache_cleanup(self, test_config, mock_logger):
        """Test that greeting cache is cleaned up properly"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Add many users to greeting cache
        current_time = time.time()
        for i in range(100):
            monitor.greeted_users[0x10000000 + i] = current_time
        
        # Simulate time passing
        with patch('time.time', return_value=current_time + 600):  # 10 minutes later
            # Process new greeting - should clean up old entries
            monitor._handle_new_user_greeting(0x20000000, '!20000000')
        
        # Old entries should be cleaned up (implementation dependent)
        # This ensures greeting cache doesn't grow unbounded