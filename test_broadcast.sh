#!/bin/bash
# MeshVM Broadcast Message Test Script
# Tests both broadcast and direct messaging functionality

MQTT_BROKER="mozart.uucp"
MESSAGE_TOPIC="meshvm/send"

echo "=== MeshVM Broadcast Message Test ==="
echo "MQTT Broker: $MQTT_BROKER"
echo "Message Topic: $MESSAGE_TOPIC"
echo ""

# Test 1: Broadcast using asterisk
echo "1. Testing broadcast with asterisk (*)..."
mosquitto_pub -h $MQTT_BROKER -t "$MESSAGE_TOPIC" -m "*@Broadcast test using asterisk - $(date)"
echo "   ✅ Sent broadcast message with *"

sleep 1

# Test 2: Broadcast using full broadcast MAC
echo "2. Testing broadcast with broadcast MAC address..."
mosquitto_pub -h $MQTT_BROKER -t "$MESSAGE_TOPIC" -m "FF:FF:FF:FF:FF:FF@Broadcast test using full MAC - $(date)"
echo "   ✅ Sent broadcast message with FF:FF:FF:FF:FF:FF"

sleep 1

# Test 3: Direct message to specific node
echo "3. Testing direct message to specific node..."
mosquitto_pub -h $MQTT_BROKER -t "$MESSAGE_TOPIC" -m "AA:BB:CC:DD:EE:FF@Direct message test - please send #status - $(date)"
echo "   ✅ Sent direct message to AA:BB:CC:DD:EE:FF"

sleep 1

# Test 4: Emergency broadcast
echo "4. Testing emergency broadcast message..."
mosquitto_pub -h $MQTT_BROKER -t "$MESSAGE_TOPIC" -m "*@EMERGENCY: All stations report status immediately - $(date)"
echo "   ✅ Sent emergency broadcast"

echo ""
echo "=== Test Complete ==="
echo ""
echo "To monitor messages in real-time, run:"
echo "mosquitto_sub -h $MQTT_BROKER -t '$MESSAGE_TOPIC' -v"
echo ""
echo "To test with the actual MeshVM system:"
echo "python3 meshvm.py -f -c test_config.conf"