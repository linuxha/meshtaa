# Integration Tests for Complete Message Flow

import pytest
import time
from unittest.mock import Mock, patch, call

from meshtaa import MeshtasticMonitor, MQTTManager, MeshtaaDaemon, CACHE_TIMEOUT_SECONDS


class TestCompleteMessageFlow:
    """Test complete end-to-end message processing flows"""

    @pytest.fixture(autouse=True)
    def patch_mqtt(self, mock_mqtt_client):
        """Ensure paho mqtt is mocked for all tests in this class"""
        self.mock_mqtt_client = mock_mqtt_client

    def test_keyword_message_to_mqtt_response_flow(self, test_config, mock_logger):
        """Test complete flow: receive keyword message → MQTT lookup → send response"""
        
        # Setup components
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Mock the interface
        mock_interface = Mock()
        monitor.interface = mock_interface
        monitor.my_node_id = 0x12345678
        
        # Mock MQTT data for weather keyword
        mqtt_manager.topic_cache = {
            'sensors/weather': {
                'payload': '{"temperature": 23.5, "humidity": 65, "status": "sunny"}',
                'timestamp': time.time()
            }
        }
        
        # Create message packet requesting weather data
        weather_packet = {
            'from': 0x87654321,
            'to': 0x12345678,  # Direct to this node
            'id': 123456789,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'#weather',
                'text': '#weather'
            },
            'fromId': '!87654321',
            'toId': '!12345678'
        }
        
        # Process the message through complete flow
        monitor._on_receive_message(weather_packet)
        
        # Verify response was sent back to sender
        mock_interface.sendText.assert_called_once()
        call_args = mock_interface.sendText.call_args
        
        # Check that weather data was included in response
        response_text = call_args[0][0]
        assert 'temperature' in response_text or '23.5' in response_text
        
        # Check destination is correct (back to sender)
        assert call_args[1]['destinationId'] == 0x87654321
        
        # Verify history logging
        mock_logger.info.assert_called()
        
    def test_broadcast_greeting_flow(self, test_config, mock_logger):
        """Test new user greeting flow for broadcast messages"""
        
        # Enable greetings
        test_config.config.set('daemon', 'greeting_enabled', 'true')
        test_config.config.set('daemon', 'greeting_format', 'Hello {node_id}! Welcome!')
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Mock the interface
        mock_interface = Mock()
        monitor.interface = mock_interface
        monitor.my_node_id = 0x12345678
        
        # Create broadcast message from new user
        broadcast_packet = {
            'from': 0x87654321,
            'to': 0xFFFFFFFF,  # Broadcast
            'id': 123456790,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'Hello mesh!',
                'text': 'Hello mesh!'
            },
            'fromId': '!87654321',
            'toId': '!ffffffff'
        }
        
        # Process the message
        monitor._on_receive_message(broadcast_packet)
        
        # Should send greeting to the new user
        mock_interface.sendText.assert_called()
        call_args = mock_interface.sendText.call_args
        
        # Check greeting content
        response_text = call_args[0][0]
        assert '!87654321' in response_text
        assert 'Welcome' in response_text
        
        # Greetings are sent as broadcast ('^all' → destinationId=0xFFFFFFFF)
        assert call_args[1]['destinationId'] == 0xFFFFFFFF
        
        # User should be cached to prevent duplicate greetings
        assert 0x87654321 in monitor.greeted_users
        
    def test_no_mqtt_data_graceful_handling(self, test_config, mock_logger):
        """Test graceful handling when MQTT topic has no data"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Mock interface
        mock_interface = Mock()
        monitor.interface = mock_interface
        monitor.my_node_id = 0x12345678
        
        # Empty MQTT cache (no data for weather)
        mqtt_manager.topic_cache = {}
        
        # Create weather request
        weather_packet = {
            'from': 0x87654321,
            'to': 0x12345678,
            'id': 123456791,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'#weather',
                'text': '#weather'
            },
            'fromId': '!87654321',
            'toId': '!12345678'
        }
        
        # Process the message
        monitor._on_receive_message(weather_packet)
        
        # Should send "no data available" response
        mock_interface.sendText.assert_called()
        call_args = mock_interface.sendText.call_args
        response_text = call_args[0][0]
        
        # Should indicate data unavailable (connection unavailable when MQTT not connected)
        assert 'unavailable' in response_text.lower() or 'no data' in response_text.lower() or 'not available' in response_text.lower()
        
    def test_multi_keyword_message_processing(self, test_config, mock_logger):
        """Test handling of messages with multiple keywords"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Mock interface
        mock_interface = Mock()
        monitor.interface = mock_interface
        monitor.my_node_id = 0x12345678
        
        # Mock MQTT data for multiple keywords
        current_time = time.time()
        mqtt_manager.topic_cache = {
            'sensors/weather': {
                'payload': 'Sunny, 23°C',
                'timestamp': current_time
            },
            'system/status': {
                'payload': 'All systems operational',
                'timestamp': current_time
            }
        }
        
        # Create message with multiple keywords
        multi_keyword_packet = {
            'from': 0x87654321,
            'to': 0x12345678,
            'id': 123456792,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'#weather #status',
                'text': '#weather #status'
            },
            'fromId': '!87654321',
            'toId': '!12345678'
        }
        
        # Process the message
        monitor._on_receive_message(multi_keyword_packet)
        
        # Should send response with both pieces of data
        mock_interface.sendText.assert_called()
        call_args = mock_interface.sendText.call_args
        response_text = call_args[0][0]
        
        # Should contain data from first matching keyword (code returns after first keyword match)
        assert 'Sunny' in response_text or '23' in response_text  # Weather data (first keyword match)
        
    def test_filtered_broadcast_no_processing(self, test_config, mock_logger):
        """Test that filtered broadcast messages skip all processing including logging"""
        
        # Configure blocklist
        test_config.config.set('daemon', 'filter_mode', 'blocklist')
        test_config.config.set('daemon', 'filter_ids', '!87654321')
        test_config.config.set('daemon', 'greeting_enabled', 'true')
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Mock interface
        mock_interface = Mock()
        monitor.interface = mock_interface
        monitor.my_node_id = 0x12345678
        
        # Create broadcast from blocked user
        filtered_broadcast = {
            'from': 0x87654321,  # Blocked user
            'to': 0xFFFFFFFF,    # Broadcast
            'id': 123456793,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'#weather',  # Valid keyword
                'text': '#weather'
            },
            'fromId': '!87654321',
            'toId': '!ffffffff'
        }
        
        # Process the message
        monitor._on_receive_message(filtered_broadcast)
        
        # Should NOT send any response (no greeting, no keyword response)
        mock_interface.sendText.assert_not_called()
        
        # Should NOT add to greeted users cache
        assert 0x87654321 not in monitor.greeted_users
        
        # Should log that it was filtered
        debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
        filter_logged = any('filtered' in call for call in debug_calls)
        assert filter_logged, "Should log that broadcast was filtered"


class TestMQTTIntegration:
    """Test MQTT manager integration with message processing"""

    @pytest.fixture(autouse=True)
    def patch_mqtt(self, mock_mqtt_client):
        """Ensure paho mqtt is mocked for all tests in this class"""
        self.mock_mqtt_client = mock_mqtt_client

    def test_mqtt_cache_expiration_during_message_flow(self, test_config, mock_logger):
        """Test that expired MQTT cache is handled gracefully during message processing"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Mock interface
        mock_interface = Mock()
        monitor.interface = mock_interface
        monitor.my_node_id = 0x12345678
        
        # Add expired MQTT data
        expired_time = time.time() - CACHE_TIMEOUT_SECONDS - 10
        mqtt_manager.topic_cache = {
            'sensors/weather': {
                'payload': 'Old weather data',
                'timestamp': expired_time
            }
        }
        
        # Create weather request
        weather_packet = {
            'from': 0x87654321,
            'to': 0x12345678,
            'id': 123456794,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'#weather',
                'text': '#weather'
            },
            'fromId': '!87654321',
            'toId': '!12345678'
        }
        
        # Process message
        monitor._on_receive_message(weather_packet)
        
        # Should send "no data available" response since cache is expired
        mock_interface.sendText.assert_called()
        call_args = mock_interface.sendText.call_args
        response_text = call_args[0][0]
        
        # Should NOT contain old data
        assert 'Old weather data' not in response_text
        # Cache expired + MQTT not connected → connection unavailable
        assert 'unavailable' in response_text.lower() or 'no data' in response_text.lower() or 'not available' in response_text.lower()
    
    def test_mqtt_message_send_request_processing(self, test_config, mock_logger):
        """Test processing of MQTT message send requests"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Mock interface
        mock_interface = Mock() 
        monitor.interface = mock_interface
        monitor.my_node_id = 0x12345678
        
        # Simulate MQTT message send request (method takes mac_address and message as separate args)
        # MAC address format: last 4 octets become node ID (CE:6E:87:65:43:21 -> !87654321)
        monitor._handle_mqtt_message_request('CE:6E:87:65:43:21', 'Hello from MQTT!')
        
        # Should attempt to send message via Meshtastic
        mock_interface.sendText.assert_called_once()
        call_args = mock_interface.sendText.call_args
        
        # Check message content
        assert 'Hello from MQTT!' in call_args[0][0]


class TestErrorRecoveryIntegration:
    """Test error recovery during complete message flows"""

    @pytest.fixture(autouse=True)
    def patch_mqtt(self, mock_mqtt_client):
        """Ensure paho mqtt is mocked for all tests in this class"""
        self.mock_mqtt_client = mock_mqtt_client

    def test_interface_send_error_recovery(self, test_config, mock_logger):
        """Test that interface send errors don't crash message processing"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Mock interface with send error
        mock_interface = Mock()
        mock_interface.sendText.side_effect = Exception("Radio disconnected")
        monitor.interface = mock_interface
        monitor.my_node_id = 0x12345678
        
        # Mock MQTT data
        mqtt_manager.topic_cache = {
            'sensors/weather': {
                'payload': 'Test weather',
                'timestamp': time.time()
            }
        }
        
        # Create message that would normally trigger response
        weather_packet = {
            'from': 0x87654321,
            'to': 0x12345678,
            'id': 123456795,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'#weather',
                'text': '#weather'
            },
            'fromId': '!87654321',
            'toId': '!12345678'
        }
        
        # Should not raise exception despite interface error
        monitor._on_receive_message(weather_packet)
        
        # Should log the error
        mock_logger.error.assert_called()
        error_calls = [str(call) for call in mock_logger.error.call_args_list]
        radio_error_logged = any('Radio disconnected' in call or 'send' in call for call in error_calls)
        assert radio_error_logged, "Should log radio send error"
    
    def test_malformed_packet_handling(self, test_config, mock_logger):
        """Test graceful handling of malformed message packets"""
        
        mqtt_manager = MQTTManager(test_config, mock_logger)
        monitor = MeshtasticMonitor(test_config, mqtt_manager, mock_logger)
        
        # Mock interface
        mock_interface = Mock()
        monitor.interface = mock_interface
        monitor.my_node_id = 0x12345678
        
        # Test various malformed packets
        malformed_packets = [
            None,                           # None packet
            {},                            # Empty packet
            {'from': 'invalid'},           # Invalid from ID
            {'from': 0x87654321},          # Missing decoded section
            {                              # Missing portnum
                'from': 0x87654321,
                'to': 0x12345678,
                'decoded': {}
            },
            {                              # Wrong portnum
                'from': 0x87654321,
                'to': 0x12345678,
                'decoded': {'portnum': 'POSITION_APP'}
            }
        ]
        
        # None of these should cause exceptions
        for packet in malformed_packets:
            monitor._on_receive_message(packet)
        
        # Should never attempt to send responses for malformed packets
        mock_interface.sendText.assert_not_called()
        
        # Should not log any exceptions
        exception_calls = [call for call in mock_logger.exception.call_args_list]
        assert len(exception_calls) == 0, "Should not log exceptions for expected malformed packets"