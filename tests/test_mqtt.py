# Test MQTT Manager and Caching Logic

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from meshtaa import MQTTManager, CACHE_TIMEOUT_SECONDS


class TestMQTTManager:
    """Test MQTT client management and topic caching"""
    
    def test_mqtt_manager_initialization(self, test_config, mock_logger, mock_mqtt_client):
        """Test that MQTT manager initializes correctly"""
        manager = MQTTManager(test_config, mock_logger)
        
        assert manager.config == test_config
        assert manager.logger == mock_logger
        assert manager.cache_timeout == CACHE_TIMEOUT_SECONDS
        assert manager.topic_cache == {}
        assert not manager.connected
    
    def test_mqtt_connection_success(self, test_config, mock_logger, mock_mqtt_client):
        """Test successful MQTT broker connection"""
        manager = MQTTManager(test_config, mock_logger)
        
        # Mock successful connection
        mock_mqtt_client.connect.return_value = 0  # Success code
        
        manager.connect()
        
        # Should attempt connection
        mock_mqtt_client.connect.assert_called_once()
        mock_mqtt_client.loop_start.assert_called_once()
    
    def test_mqtt_connection_with_authentication(self, test_config, mock_logger, mock_mqtt_client):
        """Test MQTT connection with username/password"""
        # Configure authentication
        test_config.config.set('mqtt', 'username', 'testuser')
        test_config.config.set('mqtt', 'password', 'testpass')
        
        manager = MQTTManager(test_config, mock_logger)
        
        # Should set authentication
        mock_mqtt_client.username_pw_set.assert_called_once_with('testuser', 'testpass')
    
    def test_mqtt_connection_failure_handling(self, test_config, mock_logger, mock_mqtt_client):
        """Test graceful handling of MQTT connection failures"""
        manager = MQTTManager(test_config, mock_logger)
        
        # Mock connection failure
        mock_mqtt_client.connect.side_effect = Exception("Connection failed")
        
        # Should not raise exception
        manager.connect()
        
        # Should log error
        mock_logger.error.assert_called()
    
    def test_topic_subscription_on_connect(self, test_config, mock_logger, mock_mqtt_client):
        """Test that keywords topics are subscribed on connect"""
        manager = MQTTManager(test_config, mock_logger)
        
        # subscribe() must return a tuple (result, mid)
        mock_mqtt_client.subscribe.return_value = (0, 1)  # (result, mid)
        
        # Simulate connection callback
        manager._on_connect(mock_mqtt_client, None, None, 0, None)  # rc=0 means success
        
        # Should subscribe to all keyword topics plus message topic
        expected_topics = ['sensors/weather', 'system/status', 'test/topic']
        assert mock_mqtt_client.subscribe.call_count >= len(expected_topics)
        
        # Check specific topics
        called_topics = [call[0][0] for call in mock_mqtt_client.subscribe.call_args_list]
        for topic in expected_topics:
            assert topic in called_topics
    
    def test_message_caching_on_receive(self, test_config, mock_logger, mock_mqtt_client):
        """Test that received messages are cached with timestamps"""
        manager = MQTTManager(test_config, mock_logger)
        
        # Mock message
        mock_message = Mock()
        mock_message.topic = 'sensors/weather'
        mock_message.payload.decode.return_value = '{"temp": 25, "humidity": 60}'
        
        with patch('time.time', return_value=1234567890.0):
            manager._on_message(mock_mqtt_client, None, mock_message)
        
        # Should cache the message
        assert 'sensors/weather' in manager.topic_cache
        cached = manager.topic_cache['sensors/weather']
        assert cached['payload'] == '{"temp": 25, "humidity": 60}'
        assert cached['timestamp'] == 1234567890.0
    
    def test_cache_expiration_handling(self, test_config, mock_logger, mock_mqtt_client):
        """Test that expired cache entries are handled correctly"""
        manager = MQTTManager(test_config, mock_logger)
        
        # Add expired cache entry
        old_time = time.time() - CACHE_TIMEOUT_SECONDS - 10  # 10 seconds past expiration
        manager.topic_cache['old_topic'] = {
            'payload': 'old data',
            'timestamp': old_time
        }
        
        # Should return None for expired data
        result = manager.get_topic_data('old_topic')
        assert result is None
    
    def test_cache_valid_data_retrieval(self, test_config, mock_logger, mock_mqtt_client):
        """Test retrieval of valid cached data"""
        manager = MQTTManager(test_config, mock_logger)
        
        # Add valid cache entry
        current_time = time.time()
        manager.topic_cache['current_topic'] = {
            'payload': 'current data',
            'timestamp': current_time
        }
        
        # Should return cached data
        result = manager.get_topic_data('current_topic')
        assert result == 'current data'
    
    def test_cache_missing_topic_handling(self, test_config, mock_logger, mock_mqtt_client):
        """Test handling of requests for uncached topics"""
        manager = MQTTManager(test_config, mock_logger)
        
        # Should return None for missing topic
        result = manager.get_topic_data('nonexistent_topic')
        assert result is None
    
    def test_message_sending_request_handling(self, test_config, mock_logger, mock_mqtt_client):
        """Test handling of message send requests via MQTT"""
        manager = MQTTManager(test_config, mock_logger)
        
        # Set up message callback
        callback = Mock()
        manager.set_message_callback(callback)
        
        # Mock incoming send request - format is MAC@message
        mock_message = Mock()
        mock_message.topic = 'meshtaa/send'
        mock_message.payload.decode.return_value = 'AA:BB:CC:DD:EE:FF@Hello World'
        
        # Configure the send topic
        test_config.config.set('daemon', 'message_topic', 'meshtaa/send')
        
        manager._on_message(mock_mqtt_client, None, mock_message)
        
        # Should call the message callback with (mac_addr, message) 
        callback.assert_called_once_with('AA:BB:CC:DD:EE:FF', 'Hello World')
    
    def test_message_payload_logging_truncation(self, test_config, mock_logger, mock_mqtt_client):
        """Test that long message payloads are truncated in logs"""
        manager = MQTTManager(test_config, mock_logger)
        
        # Mock long message
        long_payload = 'A' * 200  # Longer than LOG_PAYLOAD_PREVIEW_LENGTH
        mock_message = Mock()
        mock_message.topic = 'test/topic'
        mock_message.payload.decode.return_value = long_payload
        
        manager._on_message(mock_mqtt_client, None, mock_message)
        
        # Should log with truncation
        mock_logger.info.assert_called()
        log_call = mock_logger.info.call_args[0][0]
        assert '...' in log_call  # Should contain truncation marker
        assert len(log_call) < len(long_payload) + 100  # Should be much shorter than full payload


class TestMQTTManagerCacheExpiration:
    """Test cache expiration logic in detail"""
    
    def test_cache_expiration_boundary_conditions(self, test_config, mock_logger, mock_mqtt_client):
        """Test cache expiration at exact boundary"""
        manager = MQTTManager(test_config, mock_logger)
        
        # Test exactly at expiration time
        base_time = 1000.0
        expiry_time = base_time + CACHE_TIMEOUT_SECONDS
        
        manager.topic_cache['boundary_topic'] = {
            'payload': 'boundary data',
            'timestamp': base_time
        }
        
        # At exact expiry time - should still be valid
        with patch('time.time', return_value=expiry_time - 0.1):
            result = manager.get_topic_data('boundary_topic')
            assert result == 'boundary data'
        
        # Just past expiry - should be invalid
        with patch('time.time', return_value=expiry_time + 0.1):
            result = manager.get_topic_data('boundary_topic')
            assert result is None
    
    def test_cache_cleanup_on_expired_access(self, test_config, mock_logger, mock_mqtt_client):
        """Test that expired entries are removed from cache on access"""
        manager = MQTTManager(test_config, mock_logger)
        
        # Add expired entry
        old_time = time.time() - CACHE_TIMEOUT_SECONDS - 10
        manager.topic_cache['expired_topic'] = {
            'payload': 'expired data',
            'timestamp': old_time
        }
        
        # Access should clean up expired entry
        manager.get_topic_data('expired_topic')
        
        # Entry should be removed from cache
        assert 'expired_topic' not in manager.topic_cache
    
    def test_multiple_topics_expiration(self, test_config, mock_logger, mock_mqtt_client):
        """Test handling of multiple topics with different expiration times"""
        manager = MQTTManager(test_config, mock_logger)
        
        current_time = 1000.0
        
        # Add topics with different timestamps
        manager.topic_cache['fresh_topic'] = {
            'payload': 'fresh data',
            'timestamp': current_time
        }
        manager.topic_cache['stale_topic'] = {
            'payload': 'stale data', 
            'timestamp': current_time - CACHE_TIMEOUT_SECONDS - 10
        }
        
        with patch('time.time', return_value=current_time + 10):
            # Fresh should be available
            assert manager.get_topic_data('fresh_topic') == 'fresh data'
            
            # Stale should be None
            assert manager.get_topic_data('stale_topic') is None


class TestMQTTManagerErrorHandling:
    """Test error handling in MQTT operations"""
    
    def test_message_decode_error_handling(self, test_config, mock_logger, mock_mqtt_client):
        """Test that UnicodeDecodeError propagates from _on_message (no error handling currently)"""
        manager = MQTTManager(test_config, mock_logger)
        
        # Mock message with decode error
        mock_message = Mock()
        mock_message.topic = 'test/topic'
        mock_message.payload.decode.side_effect = UnicodeDecodeError('utf-8', b'', 0, 1, 'invalid')
        
        # _on_message does not wrap decode in try/except, so exception propagates
        with pytest.raises(UnicodeDecodeError):
            manager._on_message(mock_mqtt_client, None, mock_message)
    
    def test_disconnect_handling(self, test_config, mock_logger, mock_mqtt_client):
        """Test handling of MQTT disconnection"""
        manager = MQTTManager(test_config, mock_logger)
        manager.connected = True
        
        # Simulate disconnection
        manager._on_disconnect(mock_mqtt_client, None, None, 0, None)
        
        # Should update connection status
        assert not manager.connected
        
        # Should log disconnection
        mock_logger.warning.assert_called()
    
    def test_subscription_error_handling(self, test_config, mock_logger, mock_mqtt_client):
        """Test that subscription errors propagate from _on_connect (no error handling currently)"""
        manager = MQTTManager(test_config, mock_logger)
        
        # Mock subscription failure
        mock_mqtt_client.subscribe.side_effect = Exception("Subscription failed")
        
        # _on_connect does not wrap subscribe in try/except, so exception propagates
        with pytest.raises(Exception, match="Subscription failed"):
            manager._on_connect(mock_mqtt_client, None, None, 0, None)