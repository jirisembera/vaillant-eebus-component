# CLAUDE.md — Project notes for future sessions

## What this project is

Diagnostic SHIP/SPINE Python client for **Vaillant VR921/EEBUS** heat-pump
gateways. It pairs via the myVAILLANT app, runs the SHIP handshake over a
TLS WebSocket, and reads/subscribes to SPINE telemetry (Measurement, HVAC,
Setpoint, ElectricalConnection). Optional Home Assistant MQTT Discovery
publishes the values to HA.

This is a **diagnostic / hobby tool**, not production software. The user
(jirisembera@gmail.com) runs it on a Linux box on the same LAN as the
heat pump.

## Where things live

The repo is **Home-Assistant-component-first**. The integration is the primary
deliverable; the comm library `vaillant_eebus/` lives *inside* it (single source
of truth — HACS ships it with no build step), and the CLI tooling under `tools/`
reaches the same library through a committed symlink.

> **Why nested + symlink:** the library is imported relatively by the component
> (`from .vaillant_eebus import …`) and as a top-level package by the tools
> (`import vaillant_eebus`, via `tools/vaillant_eebus` → the nested dir). Both
> work from the *same* files because every internal import in the library is
> relative. There is **no `_vr921/` vendoring and no `sync_vr921.sh`** any more —
> edit `vaillant_eebus/` directly.

```
custom_components/vaillant_gateway/   # HA INTEGRATION (domain: vaillant_gateway) — PRIMARY
  __init__.py, config_flow.py, coordinator.py, entity.py, const.py,
  sensor.py, number.py, select.py, climate.py, water_heater.py,
  grouping.py, ha_meta.py, manifest.json, strings.json, translations/
  brand/                # Local HA brand images (icon/logo .png + @2x) — served
                        #   via the HA 2026.3+ brands-proxy; no brands-repo PR.
                        #   Generated from assets/brand/ (don't hand-edit PNGs)
  vaillant_eebus/       # COMM LIBRARY (no MQTT deps) — single source of truth
    __init__.py         # Exports EebusClient, EebusPairing, Update + Topology types
    client.py           # EebusClient (async ctx mgr): start()/read_values()/subscribe() API + fan-out
    session.py          # _SpineSession: receive loop + linear setup() bring-up
    pairing.py          # EebusPairing: one-shot SHIP handshake
    events.py           # Update + MeasurementUpdate / HvacModeUpdate / SetpointUpdate
    errors.py           # EebusError hierarchy (GatewayNotFound / HandshakeFailed / Write)
    keys.py             # measurement_key / setpoint_key / hvac_mode_key — the ONE source
                        #   of truth for an Update's stable slug. Handlers stamp it on emitted
                        #   Updates; build_topology precomputes it on each description node, so
                        #   `desc.key` == the value's key (consumers navigate, don't rescan).
    topology.py         # Topology / EntityInfo / FeatureInfo / DeviceIdentity dataclasses + build_topology
                        #   (EntityInfo carries DeviceClassification: user_label + model/serial;
                        #    Topology.primary_identity() / nearest_user_label() for the HA registry;
                        #    each Measurement/Setpoint/HVAC description carries its stable .key;
                        #    EntityInfo.children + Topology.roots() expose the address-nesting
                        #    tree — same objects as the flat .entities index)
    naming.py           # friendly_sensor_name, unit_to_ha, phase_label, …
    connection.py      # open_node (presence) + discover_gateway(s) + open_connection(gateway, node)
    parsers/            # PURE SPINE decoders — no value-level logging
      __init__.py       # flat re-exports (callers import `from vaillant_eebus.parsers import …`)
      _common.py        # scaled_number_to_float, coerce_list, address helpers
      discovery.py      # detailed-discovery extraction
      measurement.py    # measurementDescription/List
      hvac.py           # hvacOperationMode + SystemFunction + SetpointRelation (mode↔setpoint)
      setpoint.py       # setpointDescription/List
      electrical.py     # electricalConnectionParameterDescription
      device_classification.py  # deviceClassificationManufacturer/UserData (model/serial + user label)
    handlers/           # PER-FEATURE DISPATCH — consumes parsers, emits Updates
      __init__.py       # exports FeatureHandler, Measurement/HVAC/Setpoint/Electrical/DeviceClassificationHandler
      base.py           # FeatureHandler ABC + FeatureKey + address helpers
      measurement.py    # MeasurementHandler (subscribes, reads phase from electrical)
      hvac.py           # HVACHandler
      setpoint.py       # SetpointHandler
      electrical.py     # ElectricalHandler (read-only, exposes phase_for(ent, mid))
      device_classification.py  # DeviceClassificationHandler (read-only; per-entity model/serial + user label)
    ship.py             # SHIP framing + 5-phase handshake
    spine.py            # SPINE address/parse helpers, send_spine_read/write/call/result
    spine_replies.py    # Local responders (handle_spine_read)
    spine_requests.py   # Outgoing reads + subscribe calls
    mdns.py             # MDNSHandler, local_ipv4_addresses, resolve_interface_ipv4
    cert.py             # cert.pem/key.pem persistence → SKI
    eebus_json.py       # array-wrapped JSON ⇄ standard JSON
    utils.py            # MsgCounter (async-safe), env_*, slug

tools/                  # CLI utilities + standalone MQTT bridge (run from repo root)
  vaillant_eebus -> ../custom_components/vaillant_gateway/vaillant_eebus  # committed symlink
  pair.py               # First-time SHIP trust handshake (no SPINE loop)
  cli.py                # shared CLI plumbing (build_common_parser, …) — imported as `cli`, not runnable
  monitor.py            # `python3 tools/monitor.py` — stream live updates to stderr (no MQTT)
  info.py               # `python3 tools/info.py` — topology snapshot tool (read-only)
  write.py              # `python3 tools/write.py` — set setpoint / HVAC mode
  discover.py           # `python3 tools/discover.py` — list gateways via mDNS (read-only)
  detailed_discovery.py # `python3 tools/detailed_discovery.py` — generic SPINE probe:
                        #   dumps every feature + function the gateway reports, incl. ones
                        #   we have no handler for (drives SPINE directly, not EebusClient)
  eebus_to_mqtt/        # MQTT BRIDGE — long-running HA bridge (PYTHONPATH=tools python3 -m eebus_to_mqtt.cli)
    __init__.py         # Exports HABridge
    bridge.py           # HABridge: Update → HA Discovery + state publishes
    publisher.py        # HAMqttPublisher (paho-mqtt + topic layout)
    ha_meta.py          # guess_ha_metadata (device_class/state_class)
    cli.py              # `eebus-to-mqtt` entry — run via `PYTHONPATH=tools python3 -m eebus_to_mqtt.cli`
    mqtt_secrets.py     # Local broker creds template (imported as `from . import mqtt_secrets`)

docs/
  README.md           # Index
  architecture.md     # Layering, modules, log severities, state machines
  protocol.md         # SHIP/SPINE/EEBUS on-wire quirks (READ THIS for protocol work)
  usage.md            # CLI flags, env vars, examples
  mqtt.md             # HA MQTT Discovery topology
  troubleshooting.md  # Common failure modes
assets/brand/         # Source for the HA brand images (ORIGINAL logo, not the
  icon.svg            #   trademarked Vaillant hare): hand-drawn hare-in-oval SVG
  build.sh            #   → ImageMagick renders the 4 PNGs into the brand/ folder
README.md             # Repository overview + Home Assistant install guide
cert.pem / key.pem    # Persistent client identity (SKI binds to the gateway trust)
```

## How it runs

```bash
source .venv/bin/activate

# First-time pairing (hold while you press Trust in the myVAILLANT app)
python3 tools/pair.py [-a IPv4 | -i IFACE] [-vv]

# Long-running HA MQTT bridge
PYTHONPATH=tools python3 -m eebus_to_mqtt.cli [-a IPv4 | -i IFACE] [-vv]

# Or just stream updates to stderr (no MQTT)
python3 tools/monitor.py [-a IPv4 | -i IFACE]

# One-shot snapshot: topology tree + current values, then exit
python3 tools/info.py [-a IPv4 | -i IFACE] [--no-values]

# List the Vaillant gateways visible via mDNS, then exit (read-only)
python3 tools/discover.py [-a IPv4 | -i IFACE]
```

- `-i`/`-a` are optional. With neither, `bind_address` is `None`: zeroconf
  browses every interface (`InterfaceChoice.All`) and the client advertises its
  SHIP service on all local IPv4s (`mdns.local_ipv4_addresses`) — every
  interface covered without pinning. `-i IFACE` / `-a IPv4` pin one.
- The gateway is reached via mDNS `_ship._tcp.local.` discovery.

## Conventions used in this codebase

### Logging
- Single root handler → stderr.
- **The comm library does not log values.** `EebusClient` emits
  :class:`Update` records; consumers (the `tools/monitor.py` CLI, the HA bridge) decide
  how to surface them. `tools/monitor.py main` is the one place that prints values
  at INFO; `vaillant_eebus.parsers` is now pure parsing.
- Two trace loggers (`vaillant_eebus.ship.trace`, `vaillant_eebus.session.trace`) carry the
  raw `<` send / `>` recv frame echo. Off unless `-vvv`.
- Verbose ladder: `-v` INFO (default) / `-vv` DEBUG / `-vvv` DEBUG + trace.
  `-q` WARNING / `-qq` ERROR.

### Code style
- Stdlib + `cryptography`, `zeroconf`, `websockets`, `paho-mqtt`. No FastAPI —
  this is a flat protocol script.
- **Dataclasses at the boundaries, dicts in the plumbing.** Public records
  (`vaillant_eebus.events`, `vaillant_eebus.topology`, the parsers' `Parsed*`
  rows) are frozen dataclasses; the internal parser→handler description maps
  stay `Dict[int, Dict[str, Any]]` (uniform across all five handlers — don't
  promote those to dataclasses). That split is what "no dataclasses gymnastics"
  means — *not* "avoid dataclasses".
- **Name a value type rather than thread a positional tuple / deep nested dict
  across a boundary a consumer reads**, and factor a repeated surface into one
  shared helper/mixin instead of copy-paste (DRY). Pick readable names.
  Reference examples: `bounds.Bounds` (NamedTuple) + `bounds.setpoint_bounds`,
  the `grouped_entity.GroupedHvacModeEntity` mixin (shared climate/water_heater
  HVAC-flag surface), and `write._Writable`.
- Functions in `vaillant_eebus.spine*` and `vaillant_eebus.parsers` take keyword-only args
  for clarity (the dispatch sites are long).
- "Defensive" parsing: SPINE peers send array-wrapped, sometimes double-
  wrapped, sometimes with omitted fields. `coerce_list`, `first_cmd`, and
  the `_*` fallbacks in `vaillant_eebus.parsers` exist for that. Don't strip them.
- Emojis in user-facing log strings are intentional (this is a hobby tool).
  Don't add them to internal helpers / docstrings.

### Architectural rules
- **No polling**. Telemetry comes via SPINE subscriptions + `notify` push
  updates. Stored in the user's auto-memory:
  > Prefer event-driven SPINE/EEBUS over polling — no periodic polling
  > loops; rely on subscriptions + notify.
- Per-feature description maps are keyed by `(entity tuple, feature)`
  because Measurement IDs / HVAC IDs / Setpoint IDs are server-local and
  collide across features.
- Every received non-`result` datagram is ACK'd via `send_spine_result_ok`.
  Don't skip the ACK in new dispatch paths — the gateway will disconnect.
- Reply addresses must mirror what the peer sent (don't force-inject a
  `device` field into `addressSource` if the peer omitted it from
  `addressDestination`). `spine.make_reply_addresses` already gets this
  right.

## Smoke testing

### Lint / types / unit tests (CI gate)

Ruff + mypy + pytest run in CI (`.github/workflows/ci.yml`) on every push/PR.
Run the same gate locally (tool config lives in `pyproject.toml`):

```bash
pip install --group dev                    # PEP 735 group: runtime + pytest/ruff/mypy
ruff check . && ruff format --check .
mypy custom_components/vaillant_gateway/vaillant_eebus
pytest                                     # ~140 fast unit tests, sub-second
```

The `tests/` suite is **pure-logic only** (parsers, naming, eebus_json,
topology, spine address helpers, utils) — no network/async/HA needed. It imports
the library as a top-level `vaillant_eebus` package via the `tools/` symlink
(`pythonpath = ["tools"]`). ruff intentionally ignores `SIM105`/`SIM108` (the
defensive `try/except: pass` idiom is on purpose); mypy is deliberately lenient.

### Compile + live checks

```bash
python3 -m compileall -q custom_components/vaillant_gateway/vaillant_eebus \
    tools/*.py tools/eebus_to_mqtt
# imports resolve via the tools/ symlink:
( cd tools && python3 -c "import vaillant_eebus, eebus_to_mqtt; print(vaillant_eebus.__all__)" )

# Live: stream values for 10s then exit
timeout 10 python3 tools/monitor.py -a 192.168.1.50 --mdns-timeout 2

# Live: one-shot topology snapshot, exits when discovery + first read complete
python3 tools/info.py -a 192.168.1.50 --mdns-timeout 2
```

Beyond the `tests/` unit suite above, the integration "test" is running against
the real device; `python3 tools/monitor.py` formats updates similarly to:

```
INFO  vaillant_eebus: ⚡ Compressor Power Total: 9.0 W
INFO  vaillant_eebus: 🚿 DHW Temperature: 66.0 °C
INFO  vaillant_eebus: 🌡️ Outdoor Temperature: 19.0 °C
INFO  vaillant_eebus: 🔧 DHW Mode: auto
INFO  vaillant_eebus: 🎯 DHW Setpoint: 50.0 °C
```

## Things to know before changing things

- **First-time pairing** requires the user to press Trust in the myVAILLANT
  app while `tools/pair.py` holds HELLO `pending`. After that, `cert.pem`/
  `key.pem` carry the trust forward. `EebusClient` reuses the same handshake
  if a fresh cert is presented — pairing is conceptually optional, but the
  dedicated script makes the "press Trust now" step explicit.
- The TLS context is intentionally permissive (`CERT_NONE`, weak cipher
  list, `SECLEVEL=1`). Don't tighten without testing — Vaillant negotiates
  legacy suites. See `vaillant_eebus.connection._build_ssl_context`.
- The linear bring-up reads **every** handler's descriptions (electrical phase
  metadata included) before any values, so `MeasurementUpdate`s carry their
  phase label on the first emit. Don't reintroduce a post-hoc phase republish —
  it isn't needed any more.
- `send_ship_data` does the placeholder-replace dance to keep SPINE payloads
  from being array-wrapped twice. Mirrors ship-go exactly. Don't refactor.

## Architecture decisions (current state)

The **comm-library** decisions a change should respect —
three-step setup (presence → discover → connect via `open_node` →
`discover_gateway` → `EebusClient`), the single linear bring-up, write ownership
(handlers build a device-less `WriteFrame`; `_SpineSession.write`
is the sole transport), HA-embedding safety (shared `aiozc`, `run_in_executor`
for blocking work, no direct `zeroconf`import), and zero-config
discovery positively matched on mDNS `brand=Vaillant` — live in
`docs/architecture.md` (*Key decisions* + the module map). Keep that doc
the single source for them; don't restate it here. The **HA-integration**
decisions that doc doesn't cover are below.

- **Device identity & labels (integration side).** The comm library surfaces the
  gateway's SPINE DeviceClassification as `Topology.primary_identity()` and the
  per-zone `userData.userLabel` via `Topology.nearest_user_label` (mechanics in
  architecture.md). The integration *applies* them: `coordinator._build_snapshot`
  overlays `primary_identity()` onto the mDNS `CONF_DEVICE_*` fallback once (before
  the first entity is built, so no config-entry persistence is needed).
  `nearest_user_label` names the grouped `climate`/`water_heater` entities (it walks
  entity-address ancestors because the label sits on the parent HeatingZone, not the
  groupable HVACRoom). `naming.py` labels setpoints/modes from the device
  `entity_type` (`DHWCircuit`/`HVACRoom`/…), not entity-number guesses.
  `const.MANUFACTURER` / `UNKNOWN_MODEL` are last-resort fallbacks only.

- **Per-entity availability.** The coordinator tracks `live_keys` (reset each
  reconnect, filled by `_on_update`) and exposes `is_live(key)`; the entity base
  reports `available` only while the device is up **and** at least one of its keys
  reported on the current connection.
