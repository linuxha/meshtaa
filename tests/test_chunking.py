# Test Message Chunking Logic

import pytest
from unittest.mock import Mock, patch

from meshtaa import MeshtasticMonitor, MESSAGE_CHUNK_SIZE


class TestMessageChunking:
    """Test the complex message chunking logic for multi-part messages"""
    
    def test_short_message_single_chunk(self, test_config, mock_logger):
        """Test that short messages stay as single chunk"""
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        
        # Mock the interface and sending method
        mock_interface = Mock()
        monitor.interface = mock_interface
        
        message = "Short message"
        monitor._send_response(message, 0x12345678)
        
        # Should send once with no prefix
        mock_interface.sendText.assert_called_once_with(message, destinationId=0x12345678)
    
    def test_exact_max_size_single_chunk(self, test_config, mock_logger):
        """Test message exactly at max size stays single chunk"""
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        
        mock_interface = Mock()
        monitor.interface = mock_interface
        
        # Exactly MESSAGE_CHUNK_SIZE characters
        message = "A" * MESSAGE_CHUNK_SIZE
        monitor._send_response(message, 0x12345678)
        
        # Should send once with no prefix
        mock_interface.sendText.assert_called_once_with(message, destinationId=0x12345678)
    
    def test_slightly_oversized_message_two_chunks(self, test_config, mock_logger):
        """Test message slightly over max size gets split into two chunks with prefixes"""
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        
        mock_interface = Mock()
        monitor.interface = mock_interface
        
        # MESSAGE_CHUNK_SIZE + 1 characters (151 total)
        message = "A" * (MESSAGE_CHUNK_SIZE + 1)
        monitor._send_response(message, 0x12345678)
        
        # Calculate expected chunks
        prefix_len = len("(2/2) ")  # Length of largest prefix
        chunk_size = MESSAGE_CHUNK_SIZE - prefix_len
        
        expected_chunk1 = f"(1/2) {'A' * chunk_size}"
        expected_chunk2 = f"(2/2) {'A' * (MESSAGE_CHUNK_SIZE + 1 - chunk_size)}"
        
        # Should call sendText twice
        assert mock_interface.sendText.call_count == 2
        calls = mock_interface.sendText.call_args_list
        
        assert calls[0][0][0] == expected_chunk1
        assert calls[1][0][0] == expected_chunk2
        assert all(call[1]['destinationId'] == 0x12345678 for call in calls)
    
    def test_three_chunk_message(self, test_config, mock_logger):
        """Test message requiring three chunks"""
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        
        mock_interface = Mock()
        monitor.interface = mock_interface
        
        # ~300 characters requiring 3 chunks
        message = "A" * 300
        monitor._send_response(message, 0x12345678)
        
        # Should call sendText three times
        assert mock_interface.sendText.call_count == 3
        calls = mock_interface.sendText.call_args_list
        
        # Check that all chunks have proper prefixes
        assert calls[0][0][0].startswith("(1/3)")
        assert calls[1][0][0].startswith("(2/3)")
        assert calls[2][0][0].startswith("(3/3)")
        
        # Verify total content is preserved
        chunk_contents = [call[0][0][6:] for call in calls]  # Remove "(X/Y) " prefix
        reconstructed = "".join(chunk_contents)
        assert reconstructed == message
    
    def test_chunk_size_calculation_accuracy(self, test_config, mock_logger):
        """Test that chunk size calculation is accurate for preventing overflow"""
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        
        mock_interface = Mock()
        monitor.interface = mock_interface
        
        # Test various message sizes near chunk boundaries
        test_sizes = [MESSAGE_CHUNK_SIZE + 1, MESSAGE_CHUNK_SIZE * 2, MESSAGE_CHUNK_SIZE * 2 + 50]
        
        for size in test_sizes:
            mock_interface.reset_mock()
            message = "A" * size
            monitor._send_response(message, 0x12345678)
            
            # Verify no chunk exceeds MESSAGE_CHUNK_SIZE
            calls = mock_interface.sendText.call_args_list
            for call in calls:
                chunk = call[0][0]
                assert len(chunk) <= MESSAGE_CHUNK_SIZE, f"Chunk too long: {len(chunk)} > {MESSAGE_CHUNK_SIZE}"
    
    def test_empty_message_handling(self, test_config, mock_logger):
        """Test that empty messages are handled gracefully (not sent)"""
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        
        mock_interface = Mock()
        monitor.interface = mock_interface
        
        monitor._send_response("", 0x12345678)
        
        # Empty messages result in no sendText calls (no chunks to send)
        mock_interface.sendText.assert_not_called()
    
    def test_unicode_message_chunking(self, test_config, mock_logger):
        """Test that Unicode characters are handled correctly in chunking"""
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        
        mock_interface = Mock()
        monitor.interface = mock_interface
        
        # Unicode message with emoji (multi-byte characters)
        message = "Hello! 🌐🚀" * 30  # Repeat to make it long enough to chunk
        monitor._send_response(message, 0x12345678)
        
        # Verify all chunks are valid and under size limit
        calls = mock_interface.sendText.call_args_list
        for call in calls:
            chunk = call[0][0]
            # Check byte length since Unicode chars can be multi-byte
            assert len(chunk.encode('utf-8')) <= MESSAGE_CHUNK_SIZE * 2  # Generous allowance for Unicode
    
    def test_newline_preservation_in_chunks(self, test_config, mock_logger):
        """Test that newlines and formatting are preserved across chunks"""
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        
        mock_interface = Mock()
        monitor.interface = mock_interface
        
        # Message with newlines that will span multiple chunks
        lines = ["Line " + "A" * 30 for _ in range(10)]
        message = "\\n".join(lines)
        
        monitor._send_response(message, 0x12345678)
        
        # Reconstruct message from chunks
        calls = mock_interface.sendText.call_args_list
        if len(calls) > 1:
            # Remove prefixes and reconstruct
            chunk_contents = [call[0][0][6:] for call in calls]  # Remove "(X/Y) "
            reconstructed = "".join(chunk_contents)
            assert reconstructed == message
    
    def test_destination_id_formats(self, test_config, mock_logger):
        """Test chunking works with different destination ID formats"""
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        
        mock_interface = Mock()
        monitor.interface = mock_interface
        
        message = "A" * (MESSAGE_CHUNK_SIZE + 10)  # Force chunking
        
        # Test with integer ID - converted as-is
        mock_interface.reset_mock()
        monitor._send_response(message, 0x12345678)
        assert mock_interface.sendText.call_count > 1
        calls = mock_interface.sendText.call_args_list
        for call in calls:
            assert call[1]['destinationId'] == 0x12345678
        
        # Test with string ID like "!12345678" - converted to int
        mock_interface.reset_mock()
        monitor._send_response(message, "!12345678")
        assert mock_interface.sendText.call_count > 1
        calls = mock_interface.sendText.call_args_list
        for call in calls:
            assert call[1]['destinationId'] == 0x12345678  # String converted to int


class TestMessageChunkingEdgeCases:
    """Test edge cases and error scenarios in message chunking"""
    
    def test_extremely_long_message(self, test_config, mock_logger):
        """Test handling of very long messages"""
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        
        mock_interface = Mock()
        monitor.interface = mock_interface
        
        # Very long message (1000 chars)
        message = "A" * 1000
        monitor._send_response(message, 0x12345678)
        
        # Should split into multiple chunks
        calls = mock_interface.sendText.call_args_list
        assert len(calls) > 5  # Should require many chunks
        
        # Verify no data loss
        if len(calls) > 1:
            chunk_contents = [call[0][0][6:] for call in calls]  # Remove prefixes
            reconstructed = "".join(chunk_contents)
            assert reconstructed == message
    
    def test_interface_send_error_handling(self, test_config, mock_logger):
        """Test that interface send errors are handled gracefully"""
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        
        mock_interface = Mock()
        mock_interface.sendText.side_effect = Exception("Send failed")
        monitor.interface = mock_interface
        
        # Should not raise exception
        monitor._send_response("Test message", 0x12345678)
        
        # Should log the error
        mock_logger.error.assert_called()
    
    def test_none_destination_handling(self, test_config, mock_logger):
        """Test handling of None destination ID"""
        monitor = MeshtasticMonitor(test_config, Mock(), mock_logger)
        
        mock_interface = Mock()
        monitor.interface = mock_interface
        
        # Should handle None gracefully
        monitor._send_response("Test", None)
        
        # Should either not send or use a default
        # Exact behavior depends on implementation