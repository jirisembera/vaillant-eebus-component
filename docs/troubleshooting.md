# Troubleshooting

## "No EEBUS gateway found"

The mDNS browser timed out (`--mdns-timeout`, default 30s); the error reads
`No EEBUS gateway found (mDNS timeout after Ns).`. Diagnose:

```bash
# Is the gateway answering mDNS at all?
avahi-browse -rt _ship._tcp

# What address are we announcing on? (DEBUG line, needs -vv)
PYTHONPATH=tools python3 -m eebus_to_mqtt.cli -vv 2>&1 | grep "mDNS announced"

# Force an interface explicitly:
PYTHONPATH=tools python3 -m eebus_to_mqtt.cli -i eth0 --mdns-timeout 60 -vv
```

Common causes:
- Wrong NIC selected — autodetect picks the default-route interface; if the
  VR921 is reachable on a different interface, pass `-i` or `-a` explicitly.
- `firewalld`/`ufw` blocking UDP 5353 outbound or inbound.
- Containers / Docker bridge networks: mDNS doesn't traverse without
  `--network=host`.
- VR921 in a state where it stopped advertising — power-cycle it.

## Stuck in HELLO `pending`

This is **expected** on first run: the gateway is waiting for the user to
press **Trust** in the myVAILLANT app. The client keeps the phase alive by
re-sending `connectionHello { phase: pending }` every ~5 seconds.

If the app never shows a trust prompt:
- Make sure the phone is on the same network as the script's bind address
  (mDNS reach matters).
- Check that the phone app sees the script's `_ship._tcp.local.` advertisement
  (the SKI in the app should match `cert.pem`'s SKI).

## HELLO `aborted`

The user pressed Reject in the app, or the device decided to deny our SKI.
Delete `cert.pem` / `key.pem`, run again to mint a new identity, and retry
trust.

## Handshake fails at PROTOCOL or PIN

- `messageProtocolHandshakeError`: usually means we sent something the device
  didn't expect — bump verbosity (`-vvv`) and capture the raw frame trace.
- PIN state ≠ `none`: the spec defines a PIN exchange we don't implement.
  Devices that demand a PIN are out of scope.

## SPINE replies seem ignored / connection drops

Common cause: missing `result` ACKs. Every received non-`result` datagram
must be ACK'd promptly via `send_spine_result_ok`. The client does this
automatically (`vaillant_eebus.session._SpineSession._handle_frame`, on every
frame the receive loop decodes); if you add a new dispatch path, do not skip the ACK.

Another cause: malformed `addressSource` on a reply. See `docs/protocol.md`
— some peers reject replies that inject a `device` field they didn't ask for.

## Measurement values come through, but with empty `scope`

The measurement description list either hasn't arrived yet or arrived for a
different `(entity, feature)`. Description maps are keyed per source feature
because IDs are server-local. With `-vv` you'll see lines like:

```
✅ [MEASUREMENT] measurementDescriptionListData reply received
   Parsed N measurement descriptions
```

If you see `notify` updates **before** the description reply, the values are
correct but unlabeled until the description arrives.

## Electrical measurements have generic labels (no L1/L2/L3 phase)

Phase-aware labels ("Voltage L1", etc.) come from the `ElectricalConnection`
parameter description. The linear bring-up reads **every** feature's
descriptions (that one included) before any values, so the phase label is
already present on the first `MeasurementUpdate` — there is no post-hoc
republish, and no need to restart after first pairing.

If an electrical measurement is *stuck* on the generic label ("Voltage"), the
parameter description never arrived for its `(entity, feature)`. With `-vv`
look for `electricalConnectionParameterDescriptionListData` in the bring-up; if
it's absent the gateway didn't answer that read and the feature stays unlabeled
(the value is still correct, just generically named).

## TLS errors

The TLS context disables hostname check and certificate validation
(`ssl.CERT_NONE`) because the gateway uses a self-signed cert. The minimum
version is TLS 1.2; we set ciphers `HIGH:!aNULL:!eNULL:!MD5@SECLEVEL=1` to
accept the older suites Vaillant negotiates.

If the handshake fails with `SSL: WRONG_VERSION_NUMBER`, the websocket
upgrade is hitting a non-TLS endpoint — check `wss://<ip>:<port>/ship/`.

## MQTT publishes nothing

- `paho-mqtt` must be installed (`pip install paho-mqtt>=2.0.0`, or
  `pip install --group mqtt`). If it's missing the bridge fails fast at startup
  with `RuntimeError: paho-mqtt is required for the HA bridge` — it never runs
  silently.
- HA needs to discover the device — check the broker for retained messages
  on `homeassistant/sensor/<device_id>/+/config`.
- `SHIP_MQTT_DEBUG=true` to see every publish in the diagnostic log.

## Output is too quiet / too loud

Default level is INFO and shows **only received values**. To see everything,
use `-vv`. To see absolutely everything including raw frame bytes, `-vvv`.
The two trace loggers (`vaillant_eebus.ship.trace`, `vaillant_eebus.session.trace`) are silenced
unless `-vvv` (or `--log-level DEBUG` plus three `-v`s) is set.

## "Compressor Power Total" is stuck at single-digit Watts / no resistive-heater data

The VR921 advertises a `Measurement` feature on the Compressor entity
`[3, 1]` declaring 16 IDs (`acCurrent`, `acEnergyConsumed`, per-phase
`acPower`, `acVoltage`, …), but in practice the gateway **only ever
returns ID 9 = `acPowerTotal`**, and that one is clipped to
`valueRangeMax: 3600 W` per its own `measurementConstraintsData`.

What we verified against the live gateway:

- The 15 other declared IDs are absent from the bulk `measurementListData`
  snapshot and never arrive via `notify`.
- A filtered read of the form `{"measurementListData": {"measurementData":
  [{"measurementId": N}]}}` does **not** unlock them — the gateway echoes
  the same bulk snapshot back regardless of the filter.
- `SmartEnergyManagementPs` on `[3, 1] f19` is **inbound-only**
  (`nodeRemoteControllable: true`, `alternativesCount: 0`). It exists for
  load-management commands *to* the heat pump, not for consumption
  telemetry *from* it.
- There is no separate `DeviceDiagnosis`, `DeviceConfiguration`,
  `LoadControl`, or aux-heater entity on this gateway.

Bottom line: **the VR921 does not surface actual electrical consumption
or resistive-heater state via EEBUS.** Vaillant treats EEBUS as a
control interface (SG-Ready, demand shifting), not a metering interface.
If you observe e.g. a 6 kW draw during DHW boost, that data is coming
from a smart meter / myVAILLANT cloud / external clamp meter — not from
this protocol. Don't waste time trying to coax it out; capture it
out-of-band instead.

## The script keeps reconnecting

The receive loop uses a 60-second `wait_for` to detect idle, then pings the
peer with a `connectionHello { phase: ready }` keep-alive. If the peer
disconnects after the keep-alive, look for the "Last SHIP control message"
DEBUG dump immediately before the close — that usually identifies the cause
(e.g. an unexpected SHIP control message we didn't handle).
