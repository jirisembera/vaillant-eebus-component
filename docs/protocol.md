# SHIP / SPINE / EEBUS Protocol Notes

This file records the on-wire quirks that matter when extending the client.
The full specs live elsewhere; what's collected here is what we hit in
practice talking to real Vaillant hardware (VR921).

## EEBUS JSON ("array-wrapped")

Standard JSON object:
```json
{ "deviceInformation": { "description": { "deviceType": "EnergyManagementSystem" } } }
```

EEBUS-encoded form on the wire:
```json
[ { "deviceInformation": [ { "description": [ { "deviceType": "EnergyManagementSystem" } ] } ] } ]
```

Rules:
- Every object becomes a list of single-key objects, recursively.
- Arrays stay arrays.
- The top-level array wrapper is **stripped** (so the outermost frame is
  `{...}` even though every inner object is `[{...}]`).
- Some devices append trailing NUL bytes to payloads — strip them before parsing.

`vaillant_eebus.eebus_json.json_from_eebus_json` uses ship-go's simple byte-level
replacements (`[{`→`{`, `},{`→`,`, `}]`→`}`, `[]`→`{}`). It is good enough
for SHIP/SPINE payloads in practice; do not generalise it without testing.

When sending a SHIP Data frame containing a SPINE datagram, ship-go uses a
two-step trick to avoid double-wrapping the inner SPINE payload:

1. SPINE datagram → EEBUS JSON.
2. SHIP Data envelope with a placeholder payload `{"place":"holder"}` → EEBUS JSON.
3. Replace `[{"place":"holder"}]` in the SHIP-encoded text with the
   already-encoded SPINE payload.

`vaillant_eebus.ship.send_ship_data` mirrors this exactly.

## SHIP frame format

```
+---------------+----------------------------------+
| MessageType   | Payload (UTF-8 EEBUS JSON)       |
| 1 byte        | variable                         |
+---------------+----------------------------------+

MessageType: 0x00 = INIT (handshake start), 0x01 = Control, 0x02 = Data
```

The very first frame after WebSocket upgrade is `\x00\x00` from the client
and a CMI ack from the server (`vaillant_eebus.ship.perform_ship_handshake`).

## SHIP handshake phases

| Phase    | What happens                                                           |
|----------|------------------------------------------------------------------------|
| CMI      | server sends initial bytes (often `0000`)                              |
| HELLO    | both sides exchange `connectionHello` with `phase: ready` or `pending` |
| PROTOCOL | client sends `announceMax`, server replies `select`, client confirms   |
| PIN      | only `pinState: "none"` is supported here                              |
| ACCESS   | client sends `accessMethodsRequest`, server replies with its `id`      |

### Pending hello / pairing
On first connection the device replies with `phase: "pending"` and waits for
the user to press **Trust** in the myVAILLANT app. We must NOT advance to the
PROTOCOL phase while pending — the spec requires us to keep answering with
our own `connectionHello { phase: "pending", waiting: 60000 }` (rate-limited
to one every ~5s) until both sides flip to `ready`.

If the device replies with `phase: "aborted"`, the user denied the trust
request — give up.

### PIN
Only `none` is supported. If a device reports any other PIN state, fail —
extending PIN-based pairing would require implementing the PIN exchange the
spec defines.

## SPINE address quirks

Some Vaillant requests omit the `device` field inside `addressDestination`.
When we build replies (via `spine.make_reply_addresses`) we therefore mirror
what the peer sent — we only inject our local device address into
`addressSource` if the peer included a `device` field there. Force-injecting
can cause the peer to treat the reply as a protocol error.

Also: SPINE `cmd` is sometimes `[{...}]` and sometimes `[[{...}]]`. The
`spine.first_cmd` helper handles both shapes.

## SPINE classifiers we handle

| Classifier | Direction              | Behaviour in this client                          |
|------------|------------------------|---------------------------------------------------|
| `read`     | gateway → us           | replied to via `spine_replies.handle_spine_read`  |
| `read`     | us → gateway           | sent via `spine.send_spine_read`                  |
| `call`     | us → gateway           | used for `nodeManagementSubscriptionRequestCall`  |
| `write`    | us → gateway           | sent via `spine.send_spine_write` — setpoint / mode |
| `reply`    | gateway → us (to read) | parsed and stored / dispatched                    |
| `notify`   | gateway → us (push)    | same payload shape as reply, after subscribing    |
| `result`   | us → gateway (ACK)     | `errorNumber: 0` for every received non-result    |
| `result`   | gateway → us           | resolves the future for a pending `write`         |

Every received non-`result` datagram gets an immediate ACK. The gateway can
use `result` errors to abort the session — failing to ACK promptly causes
disconnects. We do **not** ACK inbound `result` frames — they are themselves
the ACK for a request we sent.

## Writes (setpoint / HVAC mode)

A SPINE write has the same address shape as a read against the target server
feature, but `cmdClassifier="write"` and the cmd payload carries data instead
of an empty placeholder. The gateway answers with `cmdClassifier="result"`
whose `msgCounterReference` matches the write's `msgCounter`; `errorNumber=0`
means the write was accepted.

```jsonc
// Setpoint write (e.g. DHW target = 48.0 °C)
{
  "setpointListData": {
    "setpointData": [
      { "setpointId": 1, "value": { "number": 480, "scale": -1 } }
    ]
  }
}

// HVAC operation-mode select
{
  "hvacSystemFunctionListData": {
    "hvacSystemFunctionData": [
      { "systemFunctionId": 1, "currentOperationModeId": 2 }
    ]
  }
}

// Gateway's result reply
{ "resultData": { "errorNumber": 0 } }
```

The value field uses the same `scaledNumber` encoding as the read path
(`vaillant_eebus.parsers._common.float_to_scaled_number` is the inverse of
`scaled_number_to_float`). Pick the scale that matches what the gateway
emits on the readback — DHW setpoints come back at `scale=-1` (one decimal
place), which is also the library default for `write_setpoint`.

## SPINE features we touch

What we advertise locally (`spine_replies.build_local_detailed_discovery`):

| Entity | Feature | Type                  | Role    |
|-------:|--------:|-----------------------|---------|
| 0      | 0       | NodeManagement        | special |
| 0      | 1       | DeviceClassification  | server  |
| 1      | 1       | Measurement           | client  |
| 1      | 2       | Sensing               | client  |
| 1      | 3       | HVAC                  | client  |
| 1      | 4       | Setpoint              | client  |
| 1      | 5       | ElectricalConnection  | client  |

What we discover and subscribe to on the gateway (Vaillant convention):

| Entity prefix | Function           | Notes                                          |
|---------------|--------------------|------------------------------------------------|
| `[3, 1]`      | Compressor         | Power, Energy via Measurement                  |
| `[4]`         | DHW                | Setpoints + HVAC mode + DHW temperature        |
| `[5, ...]`    | Heating circuit(s) | Setpoints + HVAC mode + room temperature       |
| `[6]`         | Outdoor sensor     | OutsideAirTemperature                          |

Both `vaillant_eebus.naming.friendly_setpoint_name` and `friendly_hvac_function_name`
fall back to this entity convention when the device's `scopeType` /
`systemFunctionType` strings are too generic to label.

## Description / value reads + subscribe

After detailed discovery names the gateway's server features, the linear
bring-up (`vaillant_eebus.session._SpineSession.setup`) runs these steps,
**once per feature class**:

1. `request_remote_functions(...)` for the description list (e.g.
   `measurementDescriptionListData`). Every handler's descriptions are read
   and awaited *before* any values, so emitted updates carry full metadata.
2. `request_remote_functions(...)` for the current value list (e.g.
   `measurementListData`) — opt-in (`read_values=True`).
3. `subscribe_remote_feature(...)` — opt-in (`subscribe=True`); a NodeManagement
   call to the remote `nodeManagementSubscriptionRequestCall`.

After subscribing, `notify` updates flow in via the subscription. **No polling
loops** — we rely entirely on subscriptions + push notifications, plus the
SHIP keep-alive `connectionHello { phase: ready }` every 60s of idle.

## Scaled numbers

SPINE `scaledNumber` is `{ "number": int, "scale": int }` representing
`number * 10^scale`. Always parse via `parsers.scaled_number_to_float`.

## Units

`degC` / `degF` are SPINE strings; consumers normalise them to `°C` / `°F`
via `vaillant_eebus.naming.unit_to_ha`. `EebusClient` applies this transformation
before emitting :class:`Update` records, so the wire form `degC` never
reaches downstream consumers.
