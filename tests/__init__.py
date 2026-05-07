# Meshtaa Test Suite
"""
Test configuration and fixtures for the Meshtaa project.

## Running Tests

Install test dependencies first:
```bash
pip install -r requirements.txt
```

Run all tests:
```bash
pytest
```

Run specific test categories:
```bash
pytest -m config          # Configuration validation tests
pytest -m chunking        # Message chunking tests  
pytest -m mqtt            # MQTT manager tests
pytest -m integration     # End-to-end message flow tests
pytest -m error_scenarios # Error handling and recovery tests
pytest -m filtering       # ID filtering behavior tests
```

Run tests with coverage (when coverage package is installed):
```bash
pytest --cov=meshtaa --cov-report=html
```

## Test Categories

- **config**: Configuration validation and loading tests
- **chunking**: Message chunking logic tests (critical area)
- **mqtt**: MQTT manager and caching functionality tests
- **integration**: Complete end-to-end message flow tests
- **error_scenarios**: Error handling, recovery, and resilience tests
- **filtering**: ID filtering behavior tests (broadcast vs direct)
- **unit**: Unit tests that don't require external dependencies

## Mock Strategy

Tests use mocks to avoid requiring actual hardware:
- `mock_serial_interface`: Simulates Meshtastic device
- `mock_mqtt_client`: Simulates MQTT broker
- `mock_logger`: Captures logging output
- Fixtures in `conftest.py` provide realistic test data

## Important Test Areas

### Configuration Validation
Tests ensure fail-fast behavior for invalid configurations:
- Missing required fields (node_id, mqtt broker)
- Invalid connection types
- Connection-specific validation (serial ports, URLs, MAC addresses)

### Message Chunking 
Tests the fragile multi-part message logic:
- Size calculations for prefixes
- Unicode handling
- Boundary conditions at MESSAGE_CHUNK_SIZE

### MQTT Caching
Tests the 5-minute TTL caching system:
- Cache expiration boundary conditions  
- Message truncation for logging
- Error handling for decode failures

### Integration Testing
Tests complete end-to-end message flows:
- Keyword message → MQTT lookup → response
- New user greeting workflows
- Multi-keyword message handling
- Broadcast vs direct message processing

### Error Scenarios
Tests system resilience and recovery:
- Device connection loss and reconnection
- MQTT broker failures and authentication errors
- Protobuf parsing error tracking and restart mechanism
- Network timeouts and connectivity issues
- Resource exhaustion and cleanup

### ID Filtering
Tests the broadcast-only filtering behavior:
- Blocklist allows direct messages but blocks broadcasts
- Allowlist never blocks direct messages
- Proper logging and processing flow
"""