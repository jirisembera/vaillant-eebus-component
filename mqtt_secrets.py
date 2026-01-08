"""Local MQTT broker credentials.

Fill in your broker settings here.

This file is intended to keep credentials out of the main script.
Recommended: add `mqtt_secrets.py` to your `.gitignore`.

Environment variables in connect_vr921.py can still override these values.
"""

# Broker connection
HA_MQTT_HOST = ""  # e.g. "192.168.1.10"
HA_MQTT_PORT = 1883

# Auth (leave empty for anonymous)
HA_MQTT_USER = "" # your user 
HA_MQTT_PASSWORD = "" # your password
