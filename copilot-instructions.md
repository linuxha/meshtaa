# MeshVM Copilot Instructions

This document provides guidance for AI development agents working with the MeshVM codebase.

## Project Overview

MeshVM is a Python daemon that bridges Meshtastic mesh networks with MQTT systems. It listens for text messages on Meshtastic devices and responds with cached MQTT data based on configurable keywords.

**Core Flow**: `Meshtastic Device ↔ MeshtasticMonitor → MQTTManager → MQTT Broker`

## Architecture

### 4-Class Modular Design
- **MeshVMConfig**: INI configuration parsing with defaults
- **MQTTManager**: MQTT broker connection, topic caching (5min TTL), message publishing  
- **MeshtasticMonitor**: Device connection (serial/network/BLE), message processing, error recovery
- **MeshVMDaemon**: Lifecycle management, signal handling, component orchestration

### Key Design Patterns
- **Dependency Injection**: Classes accept config and logger in constructors
- **Observer Pattern**: MQTT callback registration for message routing  
- **Error Recovery**: Automatic reconnection with exponential backoff
- **Caching Strategy**: 5-minute TTL for both MQTT topics and user greetings

## Code Standards

### Constants (Always Use These)
```python
CACHE_TIMEOUT_SECONDS = 300           # 5 minutes for all caching
MESSAGE_CHUNK_SIZE = 150              # Max chars per Meshtastic message  
MAX_ERRORS_PER_WINDOW = 50            # Protobuf errors before restart
ERROR_WINDOW_DURATION = 300           # Error tracking window (5 minutes)
LOG_PAYLOAD_PREVIEW_LENGTH = 50       # Characters in info logs
DEBUG_PAYLOAD_PREVIEW_LENGTH = 100    # Characters in debug logs
```

### Logging Patterns
```python
# Standard message flow logging
self.logger.info(f"Action completed - Context: value, Status: {status}")
self.logger.debug(f"Detailed info - Data: {data[:LOG_PAYLOAD_PREVIEW_LENGTH]}...")

# Error logging with context
self.logger.error(f"Operation failed - Error: {error}, Context: {context}")
self.logger.warning(f"Recoverable issue - Details: {details}, Retrying: {retry_count}")
```

### Error Handling Strategies
1. **Configuration Errors**: Raise immediately during startup (fail fast)
2. **Connection Errors**: Log + retry with backoff (recoverable)
3. **Protobuf Errors**: Count + restart daemon after threshold (safety)
4. **Message Processing**: Log + continue (don't break daemon)

### Node ID Handling
Use the established conversion functions for consistency:
- `_normalize_node_id()`: Convert any format to hex (!12345678)
- `_mac_to_node_id()`: Convert MAC address to node ID
- Support 3 formats: hex (!12345678), MAC (AA:BB:CC:DD:EE:FF), decimal (305419896)

## Key Constraints

### Hardware Dependencies
- **Real Device Required**: Most Meshtastic operations need actual hardware
- **Connection Types**: Serial (USB), Network (WiFi), BLE (Bluetooth)
- **Message Limits**: 150 characters max per message chunk
- **Network Delays**: MQTT lookups can be slow, use caching

### Configuration Requirements  
- **Required Fields**: connection_type, mqtt broker, at least one keyword
- **Path Expansion**: Use `os.path.expanduser()` for ~/ paths
- **Validation**: All config should be validated at startup

### Message Processing Rules
1. **Only TEXT_MESSAGE_APP**: Ignore other message types
2. **Broadcast vs Direct**: Handle both patterns differently  
3. **Greeting Logic**: New users only, 5-minute cache to prevent spam
4. **Filtering**: Support allowlist/blocklist by node ID - **ONLY applies to broadcast messages, direct messages are never filtered**
5. **Chunking**: Auto-split long responses with (X/Y) prefixes

## Testing Guidelines

### Mock Requirements (When Testing Framework Added)
- **Mock SerialInterface**: Use pytest fixtures for device simulation
- **Mock MQTT Client**: Use paho-mqtt test utilities  
- **Mock Time**: Control timing for cache expiration tests
- **Sample Messages**: Create realistic Meshtastic packet structures

### Critical Test Areas
- **Message Chunking**: Test multi-part message logic (fragile area)
- **ID Conversion**: All 3 formats (hex, MAC, decimal) 
- **Error Recovery**: Device disconnection, MQTT broker loss
- **Configuration**: Invalid values, missing fields, path expansion

## Common Patterns

### Adding New Keywords
1. Add to `[keywords]` section in config
2. Test MQTT topic exists and returns data
3. Verify response fits in MESSAGE_CHUNK_SIZE
4. Handle missing/empty MQTT responses gracefully

### Extending Message Processing
1. Preserve existing message flow in `_on_receive_message()`
2. Add new logic to `_process_keywords()` method
3. Use consistent logging patterns 
4. Maintain error recovery behavior

### Configuration Changes
1. Update `MeshVMConfig` class defaults
2. Add validation in constructor if required
3. Update example config file
4. Document in README.md

## Critical Code Areas (Handle With Care)

### Message Chunking Logic (lines ~1175-1190)
- Complex prefix calculation for multi-part messages
- Must recalculate chunk size after determining prefix length
- **Test thoroughly** - chunking errors break message delivery

### Protobuf Error Recovery (lines ~1235-1290) 
- Sliding window error counting for restart decisions
- Complex state machine for restart timing
- **Preserve logic** - prevents infinite restart loops

### ID Filtering System (lines ~640-760)
- Multiple normalization approaches for compatibility  
- **Consolidate carefully** - breaking compatibility affects users

## Development Workflow

### Feature Development
1. **Plan First**: Use `/plan` mode for complex changes
2. **Preserve Patterns**: Follow established logging and error handling
3. **Test Chunking**: Verify message splitting works correctly
4. **Validate Config**: Ensure configuration parsing handles new fields

### Code Review Checklist
- [ ] Constants used instead of magic numbers
- [ ] Consistent logging with context
- [ ] Error handling matches patterns (recoverable vs fatal)
- [ ] Message chunking preserved for long responses
- [ ] Configuration validation for new fields

### Debugging Tips
- **Hardware Issues**: Check `/var/log/meshvm.log` for connection errors  
- **MQTT Problems**: Verify broker connection and topic permissions
- **Message Flow**: Enable DEBUG logging to trace processing steps
- **Config Problems**: Test with `/tmp/test.conf` before deployment

## Architecture Decision Records

### Single File Design
- **Decision**: Keep all classes in meshvm.py (1,900 lines)
- **Rationale**: Simplifies deployment, testing, debugging
- **Trade-off**: Large file, but clear dependencies

### Dependency Injection
- **Decision**: Pass config and logger to all classes
- **Rationale**: Testability, clear dependencies, no globals
- **Pattern**: `Class(config: MeshVMConfig, logger: logging.Logger)`

### Caching Strategy  
- **Decision**: 5-minute TTL for all caches (MQTT topics, user greetings)
- **Rationale**: Balance freshness vs MQTT broker load
- **Implementation**: Timestamp-based expiration checking

### Error Recovery Philosophy
- **Decision**: "Blame process, not agents" - build guardrails
- **Implementation**: Restart thresholds, automatic reconnection, graceful degradation
- **User Impact**: Daemon stays running despite temporary failures

## Future Considerations

### Test Framework Integration
When pytest is added, prioritize these test categories:
1. **Unit Tests**: Each class in isolation with mocks
2. **Integration Tests**: Full message flow simulation  
3. **Error Scenario Tests**: Device disconnection, MQTT loss
4. **Configuration Tests**: All config variations and edge cases

### Performance Monitoring
Consider adding metrics for:
- Message processing latency
- MQTT cache hit rates  
- Connection stability statistics
- Error frequency tracking

---

## Quick Reference

### Essential Files
- `meshvm.py` - Main daemon (1,900 lines)
- `meshvm.conf.example` - Configuration template
- `meshvm.service` - Systemd service definition  
- `requirements.txt` - Python dependencies

### Key Methods to Understand
- `MeshtasticMonitor._on_receive_message()` - Core message processing
- `MeshtasticMonitor._process_keywords()` - MQTT lookup and response 
- `MQTTManager.get_topic_data()` - Cached topic retrieval
- `MeshVMDaemon.start()` - Complete daemon lifecycle

### Configuration Sections
- `[meshtastic]` - Device connection settings
- `[mqtt]` - Broker connection and authentication  
- `[daemon]` - Logging, filtering, greeting behavior
- `[keywords]` - Keyword to MQTT topic mappings