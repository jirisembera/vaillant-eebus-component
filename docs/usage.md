# Usage

## First-time pairing

1. Start the script connected to the same L2 network as the VR921.
2. The first connection ends up in SHIP HELLO `pending` — open the **myVAILLANT**
   app and confirm the pairing request.
3. The client persists `cert.pem` / `key.pem` in the working directory; reusing
   them keeps the SKI (and the trust relationship) stable across runs.

After pairing, subsequent runs go straight through the handshake.

## CLI

The entry points share the same network / logging / certificate flags:

```
PYTHONPATH=tools python3 -m eebus_to_mqtt.cli       Long-running HA MQTT bridge
python3 tools/monitor.py            Stream updates to stderr
python3 tools/info.py               Snapshot: topology tree + current values, exits
python3 tools/write.py              Set a writable value (setpoint or HVAC mode)
python3 tools/discover.py           List the Vaillant gateways visible via mDNS, exits
python3 tools/detailed_discovery.py Deep generic SPINE probe: dump every feature + function, exits
```

Common flags:

```
[-h]
[-a IPv4 | -i IFACE]
[--mdns-port N] [--mdns-timeout N]
[-l {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [-v ...] [-q ...]
[--log-format FMT]
[--cert-file PATH] [--key-file PATH]
```

`tools/info.py` adds its own snapshot-specific flags:

| Flag              | Default | Purpose                                              |
|-------------------|---------|------------------------------------------------------|
| `--timeout`       | 30 (s)  | Overall seconds budget for the snapshot              |
| `--no-values`     | false   | Skip the value read; print descriptions only         |

Writable items in the tree are marked with `✏️ <key>` — the key is what
`tools/write.py` accepts.

`tools/detailed_discovery.py` is the feature-agnostic counterpart to `info.py`:
it reads detailed discovery, then reads every function each feature advertises in
`supportedFunction` (and only those — nothing guessed) — including features the
library has no handler for. By default it prints a compact Device → Entity →
Feature → function tree; long entries **wrap** to `--width` with indented
continuations (no truncation), and `--raw` dumps the full JSON instead. It drives
SPINE directly (not `EebusClient`) and is read-only.

| Flag        | Default | Purpose                                          |
|-------------|---------|--------------------------------------------------|
| `--timeout` | 20 (s)  | Seconds budget for the probe once connected      |
| `--width`   | 120     | Wrap width for the tree (long lines wrap, not cut) |
| `--raw`     | false   | Dump full JSON instead of the compact tree       |

`tools/write.py` is positional:

```
python3 tools/write.py                    # list writable items + current values, exit
python3 tools/write.py <key> <value>      # write, then read back for confirmation
```

| Flag              | Default | Purpose                                              |
|-------------------|---------|------------------------------------------------------|
| `--timeout`       | 30 (s)  | Overall seconds budget for the whole run             |
| `--write-timeout` | 5 (s)   | Seconds to wait for the gateway's `result` reply     |
| `--no-confirm`    | false   | Skip the post-write read-back                        |

Key kinds are detected automatically:

- ``setpoint_…`` → ``<value>`` is parsed as a float (°C)
- ``hvacmode_…`` → ``<value>`` is the ``operationModeType`` name
  (e.g. ``heating``) or its integer id

### Choosing the network interface

By default you pass **nothing**: the client browses every interface
(`InterfaceChoice.All`) and advertises its own SHIP service on all local IPv4
addresses, so every interface is covered automatically.

The optional flags only *pin* a single interface (rarely needed — e.g. to
restrict which interface announces):
- `-a 192.168.1.50` — explicit IPv4 to advertise via mDNS and bind zeroconf to.
- `-i eth0` — interface name (resolved via SIOCGIFADDR).

`--address` and `--interface` are mutually exclusive.

### Logging verbosity

Default level is INFO and prints **only received values**. Bump for diagnostics:

| Flag   | Level                                                      |
|--------|------------------------------------------------------------|
| (none) | INFO — measurement / setpoint / HVAC mode lines only       |
| `-v`   | INFO (explicit)                                            |
| `-vv`  | DEBUG — handshake, SPINE classifier dispatch, subscriptions |
| `-vvv` | DEBUG + raw frame trace (`<` send / `>` recv)              |
| `-q`   | WARNING                                                    |
| `-qq`  | ERROR                                                      |

`--log-level DEBUG` overrides verbose/quiet. `--log-format FMT` overrides the
formatter string (default `%(asctime)s %(levelname)-7s %(name)s: %(message)s`).

### Other knobs

| Flag                  | Default      | Purpose                                      |
|-----------------------|--------------|----------------------------------------------|
| `--mdns-port`         | 54885        | Port we advertise our SHIP service on        |
| `--mdns-timeout`      | 30 (seconds) | How long to wait for the gateway via mDNS    |
| `--cert-file`         | `cert.pem`   | Persistent client cert (created if missing)  |
| `--key-file`          | `key.pem`    | Private key                                  |

## Environment variables

The CLI handles the most-used knobs; these env vars cover Home Assistant
integration and a couple of legacy switches.

| Variable                  | Default          | Effect                                                |
|---------------------------|------------------|-------------------------------------------------------|
| `HA_MQTT_HOST`            | (unset)          | If set (or `mqtt_secrets.HA_MQTT_HOST`), enable MQTT  |
| `HA_MQTT_PORT`            | 1883             |                                                       |
| `HA_MQTT_USER`            |                  |                                                       |
| `HA_MQTT_PASSWORD`        |                  |                                                       |
| `HA_MQTT_PREFIX`          | `homeassistant`  | HA Discovery topic prefix                             |
| `HA_MQTT_STATE_PREFIX`    | `ship`           | State topic prefix                                    |
| `HA_DEVICE_ID`            | `eebus_gateway`  | HA device id (slugged)                                |
| `HA_DEVICE_NAME`          | `EEBUS HeatPump` | HA device friendly name                               |
| `SHIP_MQTT_DEBUG`         | false            | Verbose MQTT publish logging (DEBUG)                  |
| `SHIP_MQTT_RETAIN_STATE`  | false            | Retain state messages (instead of just availability)  |

`mqtt_secrets.py` is read first (so secrets stay out of env / shell history);
env vars override it.

## Examples

```bash
# Default INFO output: only received values
PYTHONPATH=tools python3 -m eebus_to_mqtt.cli -a 192.168.1.50

# Full diagnostic log to a file:
PYTHONPATH=tools python3 -m eebus_to_mqtt.cli -a 192.168.1.50 -vv 2> diag.log

# Pick the interface by name:
PYTHONPATH=tools python3 -m eebus_to_mqtt.cli -i eth0

# One-shot snapshot of what the gateway exposes (no MQTT, no subscriptions):
python3 tools/info.py -a 192.168.1.50

# Same, but without polling values — descriptions only:
python3 tools/info.py -a 192.168.1.50 --no-values

# List writable items (with their keys and current values):
python3 tools/write.py -a 192.168.1.50

# Write the DHW target temperature to 48 °C (key copied from tools/info.py output):
python3 tools/write.py -a 192.168.1.50 \
    setpoint_dhwtemperaturesetpoint_e4_f4_id1 48

# Select an HVAC operation mode:
python3 tools/write.py -a 192.168.1.50 \
    hvacmode_e4_f3_sf1 heating

# With Home Assistant MQTT discovery:
HA_MQTT_HOST=192.168.0.10 HA_MQTT_USER=ha HA_MQTT_PASSWORD=secret \
    PYTHONPATH=tools python3 -m eebus_to_mqtt.cli -a 192.168.1.50
```

## Running as a service

[mqtt.md](mqtt.md#running-the-bridge-as-a-service) has a worked systemd
example for the `eebus_to_mqtt` bridge. Key points:
- Use the `.venv` Python directly in `ExecStart` (avoid sourcing).
- Drop env vars into `/etc/default/eebus-to-mqtt` and reference via
  `EnvironmentFile=`.
- `--mdns-timeout` should be > the systemd start timeout's grace window.
