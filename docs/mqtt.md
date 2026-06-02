# Home Assistant MQTT Integration

The MQTT publisher is **opt-in**: it only activates when `HA_MQTT_HOST` is
configured (env or `mqtt_secrets.py`). All published topics use Home
Assistant's MQTT Discovery convention so HA picks up sensors automatically.

## Topology

```
<HA_MQTT_PREFIX>/sensor/<device_id>/<object_id>/config       (retain) ← discovery
<HA_MQTT_STATE_PREFIX>/<device_id>/<object_id>/state                  ← values
<HA_MQTT_STATE_PREFIX>/<device_id>/availability             (retain)  ← online/offline
```

Defaults:
- `HA_MQTT_PREFIX`        = `homeassistant`
- `HA_MQTT_STATE_PREFIX`  = `ship`
- `HA_DEVICE_ID`          = `eebus_gateway` (slugged)
- `HA_DEVICE_NAME`        = `EEBUS HeatPump`

The publisher uses an MQTT LWT (Last Will and Testament) on the availability
topic so HA flips the device offline if the script crashes or loses network.

## What gets published

| Source                          | Object id pattern                                         | HA fields                                                  |
|---------------------------------|-----------------------------------------------------------|------------------------------------------------------------|
| Measurement                     | `<scope>_e<entity>_f<feat>_id<measId>`                    | `unit_of_measurement`, `device_class`, `state_class`       |
| Setpoint                        | `setpoint_<scope>_e<entity>_f<feat>_id<sid>`              | `temperature` device class                                 |
| HVAC system function (text)     | `hvacmode_e<entity>_f<feat>_sf<sid>`                      | text-valued sensor (no unit/device_class)                  |

`device_class` / `state_class` come from `eebus_to_mqtt.ha_meta.guess_ha_metadata`
based on a substring match on the SPINE `scopeType`:

| Scope contains | device_class | state_class         | default unit |
|----------------|--------------|---------------------|--------------|
| `temperature`  | temperature  | measurement         | °C           |
| `frequency`    | frequency    | measurement         | Hz           |
| `power`        | power        | measurement         | W            |
| `energy`       | energy       | total_increasing    | Wh           |
| `current`      | current      | measurement         | A            |
| `voltage`      | voltage      | measurement         | V            |

## Friendly names

`vaillant_eebus.naming.friendly_sensor_name` maps SPINE scopes to display names
(`outsideAirTemperature` → "Outdoor Temperature", `acPowerTotal` → "Compressor
Power Total"). For per-phase electrical measurements the function consults
the `ElectricalConnection` parameter description (`acMeasuredPhases`,
`acMeasurementType`) and appends `L1` / `L2` / `L3` / `Σ` plus a non-`real`
measurement type tag (e.g. "Voltage L1 reactive").

The linear bring-up reads every feature's descriptions (the
`ElectricalConnection` parameter description included) **before** any values, so
the phase-aware name is already present on the first `MeasurementUpdate` and the
HA discovery config is correct from the start — no post-hoc republish is needed.
(`HABridge._ensure_discovery` still re-sends discovery if a friendly name ever
changes, but in normal operation it never does.)

## Topic example

For a Compressor Power Total sample on entity `[3, 1]`, feature `11`,
measurement id `9` (object id = the `Update`'s stable `key`, see
`vaillant_eebus.keys`):

- discovery: `homeassistant/sensor/eebus_gateway/acpowertotal_e3_1_f11_id9/config`
- state:     `ship/eebus_gateway/acpowertotal_e3_1_f11_id9/state` → `9.0`

(The device segment is `HA_DEVICE_ID` (default `eebus_gateway`); the object id's
scope component depends on your device's reported scope name.)

## Debugging MQTT

- `SHIP_MQTT_DEBUG=true` enables per-publish DEBUG logs (topics, retain flags).
- `SHIP_MQTT_RETAIN_STATE=true` retains state messages in addition to the
  availability topic. Off by default because telemetry comes via subscription
  notifies anyway.
- The client logs `✅ [MQTT] Connected to mqtt://...` at DEBUG once the
  broker connection is up.

## Secrets

`mqtt_secrets.py` (gitignored) is the recommended place for broker
credentials:

```python
HA_MQTT_HOST = "192.168.0.10"
HA_MQTT_PORT = 1883
HA_MQTT_USER = "ha"
HA_MQTT_PASSWORD = "•••"
```

Environment variables of the same name override anything in that file.

## Running the bridge as a service

The standalone `eebus_to_mqtt` bridge is a long-running process, so on a
Raspberry Pi or Linux server it's usually managed by **systemd**. (The Home
Assistant integration needs none of this — it runs inside HA.)

1) Deploy the project (e.g. to `/opt/vaillant-eebus`) and create a venv:

```bash
sudo mkdir -p /opt/vaillant-eebus
sudo chown -R $USER: /opt/vaillant-eebus

cd /opt/vaillant-eebus
python3 -m venv .venv
source .venv/bin/activate
pip install --group mqtt    # PEP 735 group in pyproject.toml (needs pip >= 25.1 or uv)
```

2) Put the broker credentials / knobs into an env file —
`/etc/default/eebus-to-mqtt`:

```bash
# MQTT
HA_MQTT_HOST=192.168.0.10
HA_MQTT_PORT=1883
HA_MQTT_USER=ha
HA_MQTT_PASSWORD=secret

# Optional MQTT publish tuning
SHIP_MQTT_RETAIN_STATE=false
```

3) Create the unit — `/etc/systemd/system/eebus-to-mqtt.service`:

```ini
[Unit]
Description=Vaillant EEBUS → MQTT bridge
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/vaillant-eebus
EnvironmentFile=-/etc/default/eebus-to-mqtt
Environment=PYTHONPATH=/opt/vaillant-eebus/tools
ExecStart=/opt/vaillant-eebus/.venv/bin/python -m eebus_to_mqtt.cli
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

4) Enable + start, then watch the logs:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now eebus-to-mqtt.service
sudo systemctl status eebus-to-mqtt.service
journalctl -u eebus-to-mqtt.service -f
```

`--mdns-timeout` should comfortably exceed the systemd start grace window so a
slow gateway discovery doesn't trip a restart loop. See [usage.md](usage.md)
for the full flag / env-var reference.
