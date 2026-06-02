# Vaillant Gateway — Home Assistant integration (VR921 / EEBUS)

A native **Home Assistant** integration for Vaillant **VR921 / EEBUS**
heat-pump gateways. It pairs once via the **myVAILLANT** app, then talks to the
gateway entirely on your LAN — **SHIP** over a TLS WebSocket carrying **SPINE**
telemetry — and exposes the heat pump's temperatures, power/energy, hot-water
and heating-circuit controls as Home Assistant entities. `local_push`, no cloud.

> This is a diagnostic / hobby project, not affiliated with or endorsed by
> Vaillant. It has been developed against a single VR921 gateway. Use at your
> own risk.
> 
> It was also an AI coding exercise, so everything is AI-generated. 

## What you get in Home Assistant

Once paired, the integration discovers the gateway's SPINE topology and creates
entities across these platforms:

| Platform        | What it exposes                                                    |
|-----------------|--------------------------------------------------------------------|
| `sensor`        | Temperatures, compressor power, energy counters, electrical phases |
| `climate`       | Heating circuit (room setpoint + HVAC mode)                        |
| `water_heater`  | Domestic hot water (DHW) setpoint + mode                           |
| `number`        | Writable setpoints (e.g. DHW / room target temperature)            |
| `select`        | HVAC operation mode (`auto` / `heating` / …)                       |

Device identity (manufacturer, model, serial) is read from the gateway's mDNS
TXT records, so the HA device card reflects what the gateway actually reports.

## Requirements

- **Home Assistant 2024.12.0** or newer.
- The gateway reachable on the **same L2 network** as Home Assistant —
  mDNS (`_ship._tcp.local.`) must resolve between them.
- The **myVAILLANT** app, for the one-time *Trust* step during pairing.

## Installation (Home Assistant)

### Via HACS (recommended)

1. In HACS → **Integrations** → ⋮ → **Custom repositories**, add
   `https://github.com/jirisembera/vaillant-eebus-component` with category **Integration**.
2. Install **Vaillant Gateway**, then restart Home Assistant.

### Manual

Copy `custom_components/vaillant_gateway/` into your Home Assistant
`config/custom_components/` directory and restart.

### Pairing

1. The gateway is **auto-discovered** over zeroconf — Home Assistant surfaces it
   under **Settings → Devices & Services** as a discovered device. (You can also
   add it manually via **+ Add Integration → Vaillant Gateway**.)
2. The config flow shows the discovered gateway, then asks you to press
   **Trust** in the myVAILLANT app for the client `Homeassistant-<6hex>` (its
   SKI is shown in the dialog). The flow advances automatically once the gateway
   accepts the pairing.
3. The trust is bound to the client certificate's SKI and persists across
   restarts — you only press *Trust* once.

## How it works

- **SHIP handshake** over a TLS WebSocket (CMI init → HELLO → protocol
  negotiation → access methods).
- **SPINE** telemetry via **subscriptions + `notify` push** — event-driven, no
  polling loops.
- Stable client identity via the **SKI** (Subject Key Identifier) of a
  self-signed certificate the integration creates and reuses.

See [docs/architecture.md](docs/architecture.md) for the module map and state
machines, and [docs/protocol.md](docs/protocol.md) for the SHIP/SPINE/EEBUS
on-wire details.

## Repository layout

The repo is **Home-Assistant-component-first**:

- `custom_components/vaillant_gateway/` — the HA integration (primary
  deliverable), with the communication library `vaillant_eebus/` nested inside
  it as the single source of truth (shipped by HACS as-is, no build step).
- `tools/` — standalone CLI utilities and the `eebus_to_mqtt` MQTT bridge; they
  reach the same `vaillant_eebus/` library through a committed symlink.
- `docs/` — deeper documentation (see below).

`CLAUDE.md` documents the internal structure in full.

## Alternative: standalone CLI / MQTT bridge

If you don't run Home Assistant — or want to drive the gateway from the command
line — the `tools/` directory provides a standalone path that imports the same
library:

```bash
python3 -m venv .venv
source .venv/bin/activate
# Dependencies are PEP 735 groups in pyproject.toml (needs pip >= 25.1 or uv):
pip install --group cli        # CLI tools; use --group mqtt for the MQTT bridge

# First-time pairing (press Trust in the myVAILLANT app while this holds)
python3 tools/pair.py

# Stream live values to stderr
python3 tools/monitor.py

# One-shot topology + values snapshot, then exit
python3 tools/info.py
```

Other tools: `tools/write.py` (set a setpoint / HVAC mode), `tools/discover.py`
(list gateways via mDNS). A long-running **MQTT bridge** with Home Assistant
MQTT Discovery is available via `PYTHONPATH=tools python3 -m eebus_to_mqtt.cli`.

See [docs/usage.md](docs/usage.md) for all CLI flags and env vars, and
[docs/mqtt.md](docs/mqtt.md) for the MQTT bridge (topics, discovery, and a
worked systemd unit).

## Development

Linting (Ruff), type-checking (mypy) and the test suite run in CI on every push
and pull request (`.github/workflows/ci.yml`). To run the same checks locally:

```bash
source .venv/bin/activate
pip install --group dev        # PEP 735 group (pip >= 25.1 or uv); pulls in runtime too

ruff check .                                       # lint
ruff format --check .                              # formatting
mypy custom_components/vaillant_gateway/vaillant_eebus  # types (comm library)
pytest                                             # unit tests
```

The `tests/` suite covers the **pure-logic** core of the `vaillant_eebus`
library — the SPINE parsers, friendly-name/unit helpers, EEBUS-JSON conversion,
topology assembly, and the SPINE address helpers. Tests import the library as a
top-level `vaillant_eebus` package via the committed `tools/` symlink
(`pythonpath = ["tools"]` in `pyproject.toml`), so they need no Home Assistant
install. Tool configuration lives in `pyproject.toml`.

## Documentation

| Document                                           | Purpose                                                    |
|----------------------------------------------------|------------------------------------------------------------|
| [docs/architecture.md](docs/architecture.md)       | Package layout, module map, log severities, state machines |
| [docs/protocol.md](docs/protocol.md)               | SHIP / SPINE / EEBUS on-wire quirks, frames, handshake     |
| [docs/usage.md](docs/usage.md)                     | CLI flags, env vars, examples                              |
| [docs/mqtt.md](docs/mqtt.md)                       | MQTT Discovery topology, debug knobs, systemd service      |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Common failure modes and how to diagnose them              |

## Glossary

- **SHIP** — transport/session protocol over a TLS WebSocket.
- **SPINE** — the application data model carried inside SHIP DATA frames.
- **SKI** — X.509 *Subject Key Identifier* (hex), used as the stable client
  identity that the gateway's trust is bound to.
- **EEBUS JSON** — array-wrapped JSON (lists of single-key objects) that some
  SHIP/SPINE stacks expect on the wire.
