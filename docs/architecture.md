# Architecture

The repo is **Home-Assistant-component-first**. Three layers, top to bottom:

- **`custom_components/vaillant_gateway/`** — the HA integration (domain
  `vaillant_gateway`). The **primary deliverable**: config flow, coordinator,
  and the `sensor` / `number` / `select` / `climate` / `water_heater` platforms.
- **`custom_components/vaillant_gateway/vaillant_eebus/`** — the communication
  library (no MQTT / HA deps). SHIP/SPINE handshake + dispatch. Exposes
  `EebusClient`, `EebusPairing`, the `Update` event types, and the structured
  `Topology`. **Single source of truth** — HACS ships it with the component; the
  CLI tools reach the *same* files through the `tools/vaillant_eebus` symlink.
- **`tools/`** — CLI utilities (`pair`, `monitor`, `info`, `write`, `discover`,
  `detailed_discovery`) and `eebus_to_mqtt/`, a standalone MQTT-Discovery bridge
  (an *alternative* consumer to the HA component, not the main path).

This document focuses on the comm library and how a consumer drives it. The HA
integration is the reference consumer; the CLI tools and the MQTT bridge use the
identical `EebusClient` API.

## Layering

```
   ┌─────────────────────────────────────────────────────────────┐
   │ CONSUMERS                                                     │
   │  custom_components/vaillant_gateway  (coordinator + platforms)│  ← primary
   │  tools/monitor · info · write · pair    (diagnostic CLI)      │
   │  tools/eebus_to_mqtt  (Update → HA MQTT Discovery)            │  ← alternate
   └───────────────────────────────┬─────────────────────────────┘
                                    │  open_node → discover_gateway → EebusClient
   ┌────────────────────────────────▼────────────────────────────┐
   │ vaillant_eebus.client / pairing   high-level API + fan-out    │
   │ vaillant_eebus.session            one connected SPINE session │
   │ vaillant_eebus.handlers           per-feature dispatch        │
   │ vaillant_eebus.topology / events / naming / keys              │
   └───────────────────────────────┬─────────────────────────────┘
   ┌────────────────────────────────▼────────────────────────────┐
   │ vaillant_eebus.connection   presence + discovery + connect    │
   │ vaillant_eebus.ship         SHIP framing + 5-phase handshake   │
   │ vaillant_eebus.spine(_*)    SPINE framing / reads / replies    │
   │ vaillant_eebus.parsers      pure SPINE decoders                │
   │ vaillant_eebus.eebus_json   array-wrapped JSON ⇄ standard JSON │
   └─────────────────────────────────────────────────────────────┘
```

Cross-cutting helpers: `cert` (X.509/SECP256R1 → SKI), `mdns`
(`_ship._tcp.local.` discovery + interface/IP resolution), `utils` (`MsgCounter`,
env, `slug`), `errors` (the `EebusError` hierarchy).

## Public API

```python
from vaillant_eebus import EebusClient, open_node, discover_gateway

# Setup is three steps: presence (open_node) → discover → connect, then one
# linear bring-up via start().
async with open_node() as node:
    gateway = await discover_gateway(aiozc=node.aiozc)
    async with EebusClient(gateway, node=node) as hp:
        await hp.start(subscribe=True, read_values=True)  # bring-up + values + notifies
        async for update in hp.updates():                 # live stream
            ...
        # or: hp.on_change(cb) + await hp.wait_closed()
        # or snapshot: hp.values["dhwtemperature_e4_f5_id1"], hp.topology
```

The client emits an `Update` subclass (`MeasurementUpdate` / `HvacModeUpdate` /
`SetpointUpdate`) for every observable value. Each carries a stable `key` (slug)
that survives reconnects (see `vaillant_eebus.keys`).

`start()` runs the bring-up once (handshake → discovery → descriptions) and by
default does nothing more; `read_values=True` and `subscribe=True` are opt-in
(or run them later via `read_values()` / `subscribe()`). `updates()` is
**live-only** — it does not replay the `start()` snapshot, so read `hp.values`
first if you need the initial values. `start()` raises `EebusError` only on a
fatal failure (connection, handshake, or discovery — no topology); a per-feature
description/value gap is logged at ERROR and that feature is left absent from
`topology` / `values` rather than failing setup.

## Module map

Each module's docstring is the detailed reference; this is the index. The
**why** for non-obvious choices follows in *Key decisions*.

| Module | Role |
|--------|------|
| `eebus_json` | Array-wrapped JSON ⇄ standard JSON (mirrors ship-go, incl. trailing-NUL trim) |
| `ship` | SHIP framing (`0x01` Control / `0x02` Data) + the 5-phase handshake |
| `spine` | `SpineAddress` / `WriteFrame` / `SpineChannel` value types, framing + parse helpers, local-feature constants |
| `spine_replies` | Local responders to gateway-initiated reads (discovery, DeviceClassification) |
| `spine_requests` | Outgoing reads + subscription calls (take a `SpineChannel`) |
| `parsers/` | Pure per-feature SPINE decoders → typed records; **no value logging** |
| `handlers/` | One `FeatureHandler` per feature class; consume parsers, emit `Update`s, own the description maps |
| `connection` | `open_node` (presence) → `discover_gateway(s)` → `open_connection` |
| `session` | `_SpineSession`: continuous receive loop + linear `setup()` bring-up + write transport |
| `client` | `EebusClient`: async CM, public API, update fan-out, write orchestration |
| `topology` | Frozen `Topology`/`EntityInfo`/`FeatureInfo` tree + `build_topology` |
| `events` | The `Update` dataclasses |
| `keys` | The single source of truth for an `Update`'s stable slug |
| `naming` | Friendly labels + unit/phase normalization (shared by CLI + HA + bridge) |
| `pairing` | `EebusPairing`: connection setup that returns when SHIP trust completes |
| `cert` · `mdns` · `utils` · `errors` | See *Cross-cutting* above |

## Key decisions

These are the choices a change should respect that aren't obvious from one file.

- **Three-step setup: presence → discover → connect.** `open_node(...)` is the
  one setup CM — it owns the zeroconf instance, loads our cert (SKI), builds the
  TLS context, and announces our `_ship._tcp.local.` service, yielding a
  `LocalNode`. Our-side knobs (`cert/key`, `bind_address`, `mdns_port`,
  `service_name_prefix`) live here; discovery knobs (`mdns_timeout`,
  `target_ski`) live on the `discover_*` calls; `open_connection(gateway, node=…)`
  only dials. Read-only listing (`tools/discover.py`) skips the node via
  `private_aiozc(...)`.

- **One linear bring-up, no order-independent phases.** `EebusClient.start()`
  runs `_SpineSession.setup()` once, top to bottom (remote address → discovery →
  descriptions → opt-in values → opt-in subscribe). The receive loop sets the
  signalling events (`_discovery_event`, each handler's `descriptions_ready` /
  `values_ready`) as replies land, so each step sends its frames then `await`s
  the right event. Nothing re-ensures prerequisites.

- **Typed addresses, no loose tuples on the wire.** `SpineAddress` is the one
  validated parse/serialize for an on-wire address; `feature_key` yields the
  device-less `(entity, feature)` map key and `with_device` binds the gateway.
  `SpineChannel` bundles the four connection constants (`ws`, both device
  addresses, `msg_counter`) the request helpers need, so handlers take one
  channel instead of re-threading them.

- **Handlers build, the session transports.** A handler's `build_write` is a
  *pure* builder: it validates ids against its description maps, resolves the
  server via `find_server`, and returns a `WriteFrame` with a **device-less**
  destination. `_SpineSession.write` is the **sole owner of write transport** —
  it injects the connected device, allocates the `msgCounter`, registers the
  correlation future *before* sending (the gateway's `result` references that
  counter, so it must exist when the reply is processed), then sends. The
  receive loop resolves/rejects from the `result`'s `errorNumber`; timeout or
  teardown rejects any still-pending future so callers never hang.

- **Device identity from DeviceClassification, not mDNS.** mDNS TXT
  (`brand`/`model`/`id`) is the fallback; the real identity comes from the
  gateway's SPINE DeviceClassification (read by the read-only
  `DeviceClassificationHandler`) and is surfaced as `Topology.primary_identity()`.
  The per-entity `userData.userLabel` (the owner's app zone name) names the
  grouped `climate`/`water_heater` entities via `Topology.nearest_user_label`.

- **HA-embedding safety.** The library accepts HA's shared `aiozc` and only
  unregisters its own service on exit when it created the instance; blocking work
  (`cert`, `_build_ssl_context`) runs via `run_in_executor`. The HA component
  must not import `zeroconf` directly — it uses HA's shared instance +
  manifest discovery.

- **Zero-config discovery, positively matched.** `bind_address` is optional
  everywhere (`None` = browse all interfaces, announce on every local IPv4).
  Gateways are matched on mDNS TXT `brand=Vaillant` so all-interface browsing
  never latches onto another SHIP client (a second HA instance, our own CLI).

- **No polling.** Telemetry comes via SPINE subscriptions + `notify` pushes.
  `read_values()` exists for write read-back / manual one-shot polls only.

- **Every received non-`result` datagram is ACK'd** (`send_spine_result_ok`);
  skip it and the gateway disconnects. `result` frames are themselves the ACK
  and are not re-ACK'd. Reply addresses mirror what the peer sent
  (`make_reply_addresses` — see `protocol.md`).

## Per-feature description maps

Several SPINE concepts use server-local ids (Measurement / setpoint / HVAC ids)
that overlap across entities. Each handler keys its maps by **`(entity tuple,
feature)`** of the source feature — `FeatureKey`, defined in `spine` — and
exposes them read-only for `build_topology`:

```python
MeasurementHandler.description_map:  Dict[FeatureKey, Dict[int, Dict]]
HVACHandler.operation_mode_map:      Dict[FeatureKey, Dict[int, str]]
HVACHandler.system_function_map:     Dict[FeatureKey, Dict[int, Dict]]
HVACHandler.setpoint_relation_map:   Dict[FeatureKey, Dict[int, Dict[int, List[int]]]]
SetpointHandler.description_map:     Dict[FeatureKey, Dict[int, Dict]]
```

`ElectricalHandler.parameter_map` is keyed by entity tuple only, because the
ElectricalConnection feature annotates Measurement ids on the *same* entity;
`MeasurementHandler` reads it through `electrical.phase_for(entity, mid)`.

`build_topology` also folds the HVAC mode↔setpoint relations into each
`SetpointDescription.mode_type`: when an entity exposes ≥2 setpoints and an
operation mode (e.g. `eco`) selects exactly one of them, that setpoint is stamped
with the mode's type so `naming.friendly_setpoint_name` can label it. Scheduling
modes (on/off/auto, spanning several) and lone setpoints stay unlabelled.

## Logging severities

| Level | What lives here |
|-------|-----------------|
| INFO | Received values — printed by `tools/monitor.py` / logged by the bridge (the library itself does **not** emit value lines) |
| DEBUG | Diagnostic: handshake, SPINE dispatch, ACKs |
| WARNING | Unexpected message shape, soft-fails (parse fallbacks, MQTT connect) |
| ERROR | Fatal: handshake failed, websocket closed, mDNS / discovery timeout |

Verbosity ladder: `-v` INFO (default) · `-vv` DEBUG · `-vvv` DEBUG + raw frame
trace (`vaillant_eebus.ship.trace`, `vaillant_eebus.session.trace`, silenced
even at DEBUG otherwise). `-q` WARNING · `-qq` ERROR.

## State machines

### SHIP handshake (`ship.perform_ship_handshake`)
```
CMI → HELLO(ready) → [pending loop while user presses Trust in myVAILLANT] →
  HELLO(ready ↔ ready) → PROTOCOL(announceMax → select → confirm) →
  PIN(none) → ACCESS(request → methods) → DONE
```

### SPINE bring-up (`session.setup`)
A single continuous receive loop runs in the background; one `start()` drives the
linear bring-up exactly once:
```
remote address → discovery → descriptions → (values) → (subscribe)
```
- **discovery** — `nodeManagementDetailedDiscoveryData`; **fatal** on timeout
  (no topology without it).
- **descriptions** — each handler's `request_descriptions`, retried for laggards
  up to `_DESC_ATTEMPTS`; a handler still pending is logged at ERROR and left
  absent. `ElectricalConnection` / `DeviceClassification` are read-only metadata
  read in this round (they don't subscribe or read values).
- **values** / **subscribe** — opt-in (`read_values` / `subscribe`). With
  subscribe on, the receive loop then dispatches pushed `notify` updates
  indefinitely.

