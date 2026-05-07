# Test ID Filtering Behavior for Direct vs Broadcast Messages

import pytest
from unittest.mock import Mock, patch

from meshtaa import MeshtasticMonitor


class TestIDFilteringBehavior:
    """Test that ID filtering only applies to broadcast messages, not direct messages"""
    
    def test_blocklist_allows_direct_messages(self, test_config, mock_logger):
        """Test that blocklist users can still send direct messages to this node"""
        # Configure blocklist mode
        test_config.config.set('daemon', 'filter_mode', 'blocklist')
        test_config.config.set('daemon', 'filter_ids', '!87654321')
        
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        monitor.my_node_id = 0x12345678  # This node
        monitor.interface = Mock()
        
        # Mock MQTT manager to return some data
        monitor.mqtt_manager.get_topic_data = Mock(return_value="Test response")
        
        # Create direct message from blocked user to this node
        direct_packet = {
            'from': 0x87654321,      # Blocked user
            'to': 0x12345678,        # Direct to this node
            'id': 123456789,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'#weather',
                'text': '#weather'
            },
            'fromId': '!87654321',    # Blocked user
            'toId': '!12345678'       # This node
        }
        
        # Process the message - should NOT be filtered
        monitor._on_receive_message(direct_packet)
        
        # Should have processed the message and attempted to send response
        monitor.interface.sendText.assert_called()
        
        # Should log that it processed the message, not that it filtered it
        # Check that we didn't log a filter message
        filter_calls = [call for call in mock_logger.debug.call_args_list 
                       if 'filtered' in str(call)]
        assert len(filter_calls) == 0, "Direct message should not be filtered"
    
    def test_blocklist_blocks_broadcast_messages(self, test_config, mock_logger):
        """Test that blocklist users are blocked from broadcast message processing"""
        # Configure blocklist mode  
        test_config.config.set('daemon', 'filter_mode', 'blocklist')
        test_config.config.set('daemon', 'filter_ids', '!87654321')
        
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        monitor.my_node_id = 0x12345678  # This node
        monitor.interface = Mock()
        
        # Create broadcast message from blocked user
        broadcast_packet = {
            'from': 0x87654321,      # Blocked user  
            'to': 0xFFFFFFFF,        # Broadcast
            'id': 123456790,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'#weather',
                'text': '#weather' 
            },
            'fromId': '!87654321',    # Blocked user
            'toId': '!ffffffff'       # Broadcast
        }
        
        # Process the message - should be filtered
        monitor._on_receive_message(broadcast_packet)
        
        # Should NOT have attempted to send response
        monitor.interface.sendText.assert_not_called()
        
        # Should log that it filtered the broadcast message
        mock_logger.debug.assert_called()
        filter_calls = [call for call in mock_logger.debug.call_args_list 
                       if 'filtered' in str(call)]
        assert len(filter_calls) > 0, "Broadcast message should be filtered"
    
    def test_allowlist_blocks_direct_messages_never(self, test_config, mock_logger):
        """Test that allowlist mode never blocks direct messages, even from non-allowed users"""
        # Configure allowlist mode with specific allowed user
        test_config.config.set('daemon', 'filter_mode', 'allowlist')
        test_config.config.set('daemon', 'filter_ids', '!11111111')  # Different user
        
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        monitor.my_node_id = 0x12345678  # This node
        monitor.interface = Mock()
        
        # Mock MQTT manager to return some data
        monitor.mqtt_manager.get_topic_data = Mock(return_value="Test response")
        
        # Create direct message from user NOT in allowlist
        direct_packet = {
            'from': 0x87654321,      # NOT in allowlist
            'to': 0x12345678,        # Direct to this node
            'id': 123456791,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'#weather',
                'text': '#weather'
            },
            'fromId': '!87654321',    # NOT in allowlist
            'toId': '!12345678'       # This node
        }
        
        # Process the message - should NOT be filtered even though not in allowlist
        monitor._on_receive_message(direct_packet)
        
        # Should have processed the message and attempted to send response  
        monitor.interface.sendText.assert_called()
        
        # Should not log any filtering
        filter_calls = [call for call in mock_logger.debug.call_args_list 
                       if 'filtered' in str(call)]
        assert len(filter_calls) == 0, "Direct message should never be filtered, even in allowlist mode"
    
    def test_allowlist_blocks_broadcast_from_non_allowed(self, test_config, mock_logger):
        """Test that allowlist mode blocks broadcast messages from non-allowed users"""
        # Configure allowlist mode
        test_config.config.set('daemon', 'filter_mode', 'allowlist')  
        test_config.config.set('daemon', 'filter_ids', '!11111111')  # Different user
        
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        monitor.my_node_id = 0x12345678  # This node
        monitor.interface = Mock()
        
        # Create broadcast message from user NOT in allowlist
        broadcast_packet = {
            'from': 0x87654321,      # NOT in allowlist
            'to': 0xFFFFFFFF,        # Broadcast
            'id': 123456792,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'#weather',
                'text': '#weather'
            },
            'fromId': '!87654321',    # NOT in allowlist
            'toId': '!ffffffff'       # Broadcast
        }
        
        # Process the message - should be filtered
        monitor._on_receive_message(broadcast_packet)
        
        # Should NOT have attempted to send response
        monitor.interface.sendText.assert_not_called()
        
        # Should log that it filtered the broadcast message
        filter_calls = [call for call in mock_logger.debug.call_args_list 
                       if 'filtered' in str(call)]
        assert len(filter_calls) > 0, "Broadcast message should be filtered when not in allowlist"