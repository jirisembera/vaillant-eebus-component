"""connect_vr921.py

Diagnostic SHIP/SPINE client for Vaillant VR921/EEBUS devices.

High-level flow (what this script does):
1) Create or reuse a persistent client certificate (identity is the certificate SKI).
2) Announce a local SHIP service via mDNS (`_ship._tcp.local.`) so the Vaillant app/device
     can discover the client for trust/pairing.
3) Discover the VR921 via mDNS and connect to its SHIP websocket (`wss://<ip>:<port>/ship/`).
4) Perform the SHIP handshake (HELLO, protocol selection, PIN state, access methods).
5) Exchange SPINE datagrams inside SHIP DATA frames:
     - Reply to gateway-initiated READ requests (keeps the session alive)
     - Request remote detailed discovery and use-case data
     - Subscribe to Measurement servers and read/publish measurements

Home Assistant / MQTT:
- If `HA_MQTT_HOST` (or mqtt_secrets.py) is configured, the script publishes Home Assistant
    MQTT Discovery config + state updates.

Interoperability notes:
- SHIP/EEBUS JSON is often "array-wrapped" (list of single-key objects). Helpers in this file
    convert between standard JSON and that on-wire format.
- Vaillant devices can be timing/format strict; a lot of logic here is defensive.
"""

import ssl
import socket
import datetime
import asyncio
import logging
import json
import os
import time
import re
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple, cast
from zeroconf.asyncio import AsyncZeroconf, AsyncServiceInfo, AsyncServiceBrowser
from zeroconf import IPVersion, ServiceListener
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------------------------
# Utilities / small helpers
# ---------------------------------------------------------------------------


class MsgCounter:
    """Async-safe SPINE msgCounter generator.

    SPINE datagrams contain a monotonically increasing msgCounter.
    We guard increments with a lock because this script may send messages from
    different async code paths.
    """

    def __init__(self, start: int = 1):
        self._value = start
        self._lock = asyncio.Lock()

    async def next(self) -> int:
        async with self._lock:
            value = self._value
            self._value += 1
            # SPINE msgCounter is uint64 and may overflow; for this diagnostic client,
            # a simple increment is sufficient.
            return value


def _env_str(name: str, default: str = "") -> str:
    """Read an environment variable as a string."""
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v)


def _env_int(name: str, default: int) -> int:
    """Read an environment variable as an int (with fallback)."""
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    """Read an environment variable as a boolean.

    Accepts common truthy/falsey strings like 1/0, true/false, yes/no.
    """
    v = os.environ.get(name)
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _slug(s: str) -> str:
    """Turn a string into a safe MQTT/Home-Assistant object_id component."""
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"


def _unit_to_ha(unit: Any) -> str:
    """Normalize SPINE unit representations to Home Assistant-friendly strings."""
    if unit is None:
        return ""
    if isinstance(unit, str):
        u = unit.strip()
        # SPINE/EEBUS commonly uses symbolic unit strings (e.g. "degC").
        # Home Assistant expects specific unit strings for some device classes.
        if u == "degC":
            return "°C"
        if u == "degF":
            return "°F"
        return u
    if isinstance(unit, dict):
        u = unit.get("unit") or unit.get("name")
        return _unit_to_ha(str(u)) if u is not None else ""
    return str(unit)


def _guess_ha_metadata(scope_type: str, unit: str) -> Dict[str, str]:
    """Best-effort mapping to Home Assistant sensor metadata.

    Keep it simple; HA can still show the sensor without these.
    """
    s = (scope_type or "").lower()
    u = (unit or "").strip()

    if "temperature" in s:
        return {"device_class": "temperature", "state_class": "measurement", "unit": u or "°C"}
    if "power" in s:
        return {"device_class": "power", "state_class": "measurement", "unit": u or "W"}
    if "energy" in s:
        return {"device_class": "energy", "state_class": "total_increasing", "unit": u or "Wh"}
    if "current" in s:
        return {"device_class": "current", "state_class": "measurement", "unit": u or "A"}
    if "voltage" in s:
        return {"device_class": "voltage", "state_class": "measurement", "unit": u or "V"}
    return {"device_class": "", "state_class": "measurement", "unit": u}


def _friendly_sensor_name(scope_type: str, *, source_entity: Optional[list[int]] = None) -> str:
    """Return a human-friendly sensor name for Home Assistant."""
    s = (scope_type or "").strip()
    low = s.lower()

    # Known Vaillant/VR921 scopes
    if low == "outsideairtemperature":
        return "Außentemperatur"
    if low == "dhwtemperature":
        return "Warmwasser Temperatur"
    if low == "roomairtemperature":
        return "Raumtemperatur"
    if low == "acpowertotal":
        return "Kompressor Leistung"
    if low.startswith("acpower"):
        return "Leistung"
    if "temperature" in low:
        return "Temperatur"

    # Fallback: keep scope, optionally annotate source entity
    if source_entity:
        return f"{s} (entity={source_entity})"
    return s or "Messwert"


class HAMqttPublisher:
    """Optional MQTT publisher for Home Assistant Discovery.

    Enabled when HA_MQTT_HOST is set. Requires paho-mqtt.
    """

    def __init__(self, *, device_id: str, device_name: str):
        self.device_id = device_id
        self.device_name = device_name
        self.base_prefix = _env_str("HA_MQTT_PREFIX", "homeassistant").strip("/")
        self.state_prefix = _env_str("HA_MQTT_STATE_PREFIX", "ship").strip("/")

        # Broker credentials:
        # 1) Prefer local file `mqtt_secrets.py` (keeps secrets out of main script)
        # 2) Allow environment variables to override
        secrets_host = ""
        secrets_port = 1883
        secrets_user = ""
        secrets_password = ""
        try:
            import mqtt_secrets as _mqtt_secrets  # type: ignore

            secrets_host = str(getattr(_mqtt_secrets, "HA_MQTT_HOST", "") or "").strip()
            secrets_port = int(getattr(_mqtt_secrets, "HA_MQTT_PORT", 1883) or 1883)
            secrets_user = str(getattr(_mqtt_secrets, "HA_MQTT_USER", "") or "").strip()
            secrets_password = str(getattr(_mqtt_secrets, "HA_MQTT_PASSWORD", "") or "")
        except Exception:
            # Missing/invalid secrets file is OK; env vars can still configure MQTT.
            pass

        # If neither secrets nor env provides a host, MQTT stays disabled.
        self.host = _env_str("HA_MQTT_HOST", secrets_host).strip()
        self.port = _env_int("HA_MQTT_PORT", secrets_port)
        self.username = _env_str("HA_MQTT_USER", secrets_user)
        self.password = _env_str("HA_MQTT_PASSWORD", secrets_password)
        self.enabled = bool(self.host)
        self.debug = _env_bool("SHIP_MQTT_DEBUG", False)
        self.retain_state = _env_bool("SHIP_MQTT_RETAIN_STATE", False)
        self._discovered_object_ids: set[str] = set()
        self._mqtt = None

    def connect(self) -> None:
        """Connect to the MQTT broker (best-effort).

        If MQTT isn't configured (no host), this is a no-op.
        Uses an LWT (will) message so Home Assistant sees us as offline if we crash.
        """
        if not self.enabled:
            return
        try:
            import paho.mqtt.client as mqtt  # type: ignore
        except Exception:
            print(
                "⚠️  [MQTT] HA_MQTT_HOST gesetzt, aber 'paho-mqtt' fehlt. Installieren mit: pip3 install paho-mqtt"
            )
            self.enabled = False
            return

        # paho-mqtt 2.x deprecates callback API v1; opt into v2 when available.
        client_kwargs: Dict[str, Any] = {"client_id": f"ship-{self.device_id}"}
        cb_api = getattr(mqtt, "CallbackAPIVersion", None)
        if cb_api is not None:
            try:
                client_kwargs["callback_api_version"] = cb_api.VERSION2
            except Exception:
                pass

        client = mqtt.Client(**client_kwargs)
        if self.username:
            client.username_pw_set(self.username, self.password or None)
        # Ensure HA sees us go offline even on crashes/network loss.
        try:
            client.will_set(self._topic_availability(), payload="offline", qos=0, retain=True)
        except Exception:
            pass
        try:
            client.connect(self.host, self.port, keepalive=30)
            client.loop_start()
            self._mqtt = client
            self.publish_availability(True)
            print(f"✅ [MQTT] Connected to mqtt://{self.host}:{self.port}")
            if self.debug:
                print(
                    "🔎 [MQTT] Debug enabled. "
                    f"device_id={self.device_id} discovery_prefix={self.base_prefix} state_prefix={self.state_prefix} "
                    f"availability_topic={self._topic_availability()}"
                )
        except Exception as e:
            print(f"⚠️  [MQTT] Connect failed: {e}")
            self.enabled = False
            self._mqtt = None

    def close(self) -> None:
        """Publish offline availability and close MQTT cleanly."""
        if not self.enabled or self._mqtt is None:
            return
        try:
            self.publish_availability(False)
        except Exception:
            pass
        try:
            self._mqtt.loop_stop()
            self._mqtt.disconnect()
        except Exception:
            pass
        self._mqtt = None

    def _topic_availability(self) -> str:
        return f"{self.state_prefix}/{self.device_id}/availability"

    def publish_availability(self, online: bool) -> None:
        if not self.enabled or self._mqtt is None:
            return
        if self.debug:
            print(f"📤 [MQTT] availability {self._topic_availability()} = {'online' if online else 'offline'} (retain)")
        self._mqtt.publish(
            self._topic_availability(),
            payload=("online" if online else "offline"),
            qos=0,
            retain=True,
        )

    def _topic_state(self, object_id: str) -> str:
        return f"{self.state_prefix}/{self.device_id}/{object_id}/state"

    def _topic_config(self, object_id: str) -> str:
        return f"{self.base_prefix}/sensor/{self.device_id}/{object_id}/config"

    def ensure_discovery(self, *, object_id: str, name: str, unit: str, device_class: str, state_class: str) -> None:
        """Publish Home Assistant MQTT Discovery config for a sensor (once).

        HA listens on `<discovery_prefix>/sensor/<device>/<object_id>/config`.
        Publishing config once is enough; state updates use a separate state topic.
        """
        if not self.enabled or self._mqtt is None:
            return
        if object_id in self._discovered_object_ids:
            return

        payload: Dict[str, Any] = {
            "name": name,
            "unique_id": f"{self.device_id}_{object_id}",
            "state_topic": self._topic_state(object_id),
            "availability_topic": self._topic_availability(),
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "identifiers": [self.device_id],
                "name": self.device_name,
            },
        }
        if unit:
            payload["unit_of_measurement"] = unit
        if device_class:
            payload["device_class"] = device_class
        if state_class:
            payload["state_class"] = state_class

        if self.debug:
            print(f"📤 [MQTT] discovery {self._topic_config(object_id)} (retain)")
        self._mqtt.publish(self._topic_config(object_id), json.dumps(payload, ensure_ascii=False), qos=0, retain=True)
        self._discovered_object_ids.add(object_id)

    def publish_state(self, *, object_id: str, value: float) -> None:
        """Publish the current sensor value to the MQTT state topic."""
        if not self.enabled or self._mqtt is None:
            return
        # Publish as plain number (best for HA sensors)
        if self.debug:
            print(f"📤 [MQTT] state {self._topic_state(object_id)} = {value}")
        self._mqtt.publish(self._topic_state(object_id), payload=str(value), qos=0, retain=self.retain_state)


def json_into_eebus_json(payload: Any) -> str:
    """Convert standard JSON objects into the EEBUS array-wrapped JSON format.

    This mirrors the behavior of ship-go's JsonIntoEEBUSJson():
    - Objects (dict) become arrays of single-key objects, recursively.
    - Arrays stay arrays.
    - The top-level array wrapper is stripped.
    """

    def _to_eebus(value: Any) -> Any:
        # ship-go uses an ordered map to preserve field order. Python 3.7+
        # preserves dict insertion order, and we additionally use OrderedDict
        # when parsing from JSON text.
        if isinstance(value, (dict, OrderedDict)):
            return [{k: _to_eebus(v)} for k, v in value.items()]
        if isinstance(value, list):
            return [_to_eebus(v) for v in value]
        return value

    converted = _to_eebus(payload)
    text = json.dumps(converted, separators=(",", ":"), ensure_ascii=False)

    # ship-go trims the first/last bracket (top-level is expected to be an object)
    if text.startswith("[") and text.endswith("]"):
        return text[1:-1]
    return text


# ---------------------------------------------------------------------------
# EEBUS JSON compatibility helpers
# ---------------------------------------------------------------------------
# Many SHIP/SPINE implementations don't send "plain" JSON objects on the wire.
# Instead, they represent objects as a list of single-key objects, recursively.
# The functions below convert between normal JSON and that array-wrapped format.
# ---------------------------------------------------------------------------


def json_text_into_eebus_json(payload_text: str) -> str:
    """Convert a JSON text into EEBUS JSON using ship-go's ordering approach."""
    parsed = json.loads(payload_text, object_pairs_hook=OrderedDict)
    return json_into_eebus_json(parsed)


def json_from_eebus_json(payload_text: str) -> str:
    """Convert EEBUS array-wrapped JSON into standard JSON.

    ship-go uses a simple replacement strategy that works for SHIP/SPINE payloads.
    We apply the same rules and also trim trailing NUL bytes (some devices append 0x00).
    """
    b = payload_text.encode("utf-8", errors="ignore")
    b = b.replace(b"[{", b"{")
    b = b.replace(b"},{", b",")
    b = b.replace(b"}]", b"}")
    b = b.replace(b"[]", b"{}")
    b = b.strip(b"\x00")
    return b.decode("utf-8", errors="ignore")


def _first_cmd(payload_cmd: Any) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of the first SPINE cmd object."""
    if isinstance(payload_cmd, dict):
        return payload_cmd

    if not isinstance(payload_cmd, list) or not payload_cmd:
        return None

    first = payload_cmd[0]
    # Sometimes cmd is [[{...}]]
    if isinstance(first, list) and first:
        inner = first[0]
        return inner if isinstance(inner, dict) else None
    return first if isinstance(first, dict) else None


def _parse_spine_datagram(message: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return (header, first_cmd) from a decoded SHIP data message."""
    data = message.get("data")
    if not isinstance(data, dict):
        return None
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return None
    datagram = payload.get("datagram")
    if not isinstance(datagram, dict):
        return None

    header = datagram.get("header")
    if not isinstance(header, dict):
        return None

    d_payload = datagram.get("payload")
    if not isinstance(d_payload, dict):
        return None

    cmd = _first_cmd(d_payload.get("cmd"))
    if cmd is None:
        return None

    return header, cmd


def _make_spine_reply_addresses(
    request_header: Dict[str, Any],
    *,
    local_device_address: str,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return (address_source, address_destination) for replies/results.

    SPINE replies swap source/destination. Some devices omit the device field
    in addressDestination in requests; we always set our device on addressSource.
    """
    address_destination = request_header.get("addressSource")
    address_source = request_header.get("addressDestination")
    if not isinstance(address_destination, dict) or not isinstance(address_source, dict):
        return None

    address_source = dict(address_source)
    # Important interop detail:
    # Some devices omit the "device" field in addressDestination when addressing the local side.
    # For result/reply messages, mirror the structure the peer used (do not force-inject "device"
    # unless it was present), otherwise some peers treat it as a protocol error.
    if "device" in address_source:
        address_source["device"] = local_device_address

    return address_source, dict(address_destination)


# ---------------------------------------------------------------------------
# Certificate / identity
# ---------------------------------------------------------------------------

def get_or_create_certificate():
    """Create or reuse a local client certificate and return its SKI (hex).

    Why this matters:
    - The Vaillant trust/pairing flow is tied to the client certificate identity.
    - Reusing the same cert keeps the SKI stable across runs.

    Files created/used:
    - cert.pem / key.pem in the current working directory.
    """
    cert_file, key_file = "cert.pem", "key.pem"
    if os.path.exists(cert_file) and os.path.exists(key_file):
        with open(cert_file, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        ski = cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest.hex()
        print(f"🔄 Zertifikat wiederverwendet (SKI: {ski})")
        return ski
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"EEBUS-Python-Client")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(
            key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(
            now - datetime.timedelta(days=1)).not_valid_after(
            now + datetime.timedelta(days=3650)
        ).add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False).sign(key, hashes.SHA256())
        with open(cert_file, "wb") as f: f.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(key_file, "wb") as f: f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        ski = cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest.hex()
        print(f"📜 Neues Zertifikat erstellt (SKI: {ski})")
        return ski

class MDNSHandler(ServiceListener):
    """Collect the first discovered `_ship._tcp.local.` service that isn't ourselves.

    The VR921 advertises itself via mDNS. We listen for services and keep the first
    candidate in `target_info`.
    """

    def __init__(self, ski):
        self.ski, self.target_info = ski, None
    def add_service(self, zc, type, name):
        asyncio.ensure_future(self.async_add_service(zc, type, name))
    async def async_add_service(self, zc, type, name):
        """Async callback invoked by Zeroconf when a new service appears."""
        info = await zc.async_get_service_info(type, name)
        if info and b"ski" in info.properties:
            try:
                remote_ski = info.properties.get(b"ski", b"").decode("utf-8")
            except Exception:
                remote_ski = ""
            if remote_ski and remote_ski == self.ski:
                return
            if not self.target_info: self.target_info = info
    def remove_service(self, zc, type, name): pass
    def update_service(self, zc, type, name): pass

async def send_ship_json(ws, data):
    """Sendet SHIP-Control JSON (MessageType=0x01) im EEBUS JSON-Format."""
    # SHIP Control frames are: 0x01 + (EEBUS JSON payload)
    eebus_text = json_into_eebus_json(data)
    msg = b"\x01" + eebus_text.encode("utf-8")
    await ws.send(msg)
    print(f"📤 Gesendet: {list(data.keys())}")


async def send_ship_data(ws, data):
    """Sendet SHIP-Data JSON (MessageType=0x02) im ship-go kompatiblen Format.

    ship-go serialisiert SPINE in zwei Schritten:
    1) SPINE Datagramm -> EEBUS JSON
    2) SHIP Data Envelope (mit placeholder payload) -> EEBUS JSON
       und danach payload wieder als RawMessage hineinpflastern.

    Damit bleibt das SHIP payload-Objekt unverändert (wird nicht nochmal array-wrapped).
    """

    # SHIP Data frames are: 0x02 + (EEBUS JSON payload)
    # The payload is itself a SHIP "data" envelope containing a SPINE datagram.

    # Expect data to be a standard JSON SPINE message like: {"datagram": {...}}
    spine_std = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    spine_eebus = json_text_into_eebus_json(spine_std)

    payload_placeholder = '{"place":"holder"}'
    ship_std_obj = {
        "data": {
            "header": {"protocolId": "ee1.0"},
            "payload": json.loads(payload_placeholder),
        }
    }
    ship_std = json.dumps(ship_std_obj, separators=(",", ":"), ensure_ascii=False)
    ship_eebus = json_text_into_eebus_json(ship_std)

    # ship-go replaces `[payloadPlaceholder]` with the already-transformed SPINE payload
    ship_eebus = ship_eebus.replace(f"[{payload_placeholder}]", spine_eebus)

    msg = b"\x02" + ship_eebus.encode("utf-8")
    await ws.send(msg)


async def send_access_methods(ws, local_ship_id: str):
    """Send the SHIP accessMethods response, which carries our local SHIP id."""
    await send_ship_json(ws, {"accessMethods": {"id": local_ship_id}})


def build_local_detailed_discovery(local_device_address: str) -> Dict[str, Any]:
    """Minimal NodeManagementDetailedDiscoveryData reply.

    Enough to let a remote device identify us and keep the session alive.
    """
    return {
        "specificationVersionList": {"specificationVersion": ["1.3.0"]},
        "deviceInformation": {
            "description": {
                "deviceAddress": {"device": local_device_address},
                "deviceType": "EnergyManagementSystem",
                "featureSet": "smart",
                "brandName": "Python",
                "deviceModel": "SHIP-Layer1",
                "serialNumber": local_device_address,
                "deviceCode": "python-ship",
            }
        },
        "entityInformation": [
            {
                "description": {
                    "entityAddress": {"entity": [0]},
                    "entityType": "DeviceInformation",
                    "description": "DeviceInformation",
                }
            },
            {
                "description": {
                    "entityAddress": {"entity": [1]},
                    "entityType": "CEM",
                    "description": "CEM",
                }
            },
        ],
        "featureInformation": [
            {
                "description": {
                    "featureAddress": {"entity": [0], "feature": 0},
                    "featureType": "NodeManagement",
                    "role": "special",
                    "description": "NodeManagement",
                }
            },
            {
                "description": {
                    "featureAddress": {"entity": [0], "feature": 1},
                    "featureType": "DeviceClassification",
                    "role": "server",
                    "description": "DeviceClassification",
                }
            },
            {
                "description": {
                    "featureAddress": {"entity": [1], "feature": 1},
                    "featureType": "Measurement",
                    "role": "client",
                    "description": "MeasurementClient",
                }
            },
            {
                "description": {
                    "featureAddress": {"entity": [1], "feature": 2},
                    "featureType": "Sensing",
                    "role": "client",
                    "description": "SensingClient",
                }
            },
        ],
    }


def build_device_classification_manufacturer_data(local_device_address: str) -> Dict[str, Any]:
    """Minimal DeviceClassificationManufacturerData.

    Keep values simple/consistent; VR921 mostly needs something parseable.
    """
    return {
        "deviceName": "SHIP Python Client",
        "deviceCode": "python-ship",
        "brandName": "Python",
        "powerSource": "mains3Phase",
        "serialNumber": local_device_address,
    }


def build_device_classification_user_data() -> Dict[str, Any]:
    """Minimal DeviceClassificationUserData."""
    return {
        "deviceName": "SHIP Python Client",
    }


def _spine_addr(*, device: str, entity: int, feature: int) -> Dict[str, Any]:
    """Convenience builder for a SPINE feature address (device/entity/feature)."""
    return {"device": device, "entity": [entity], "feature": feature}


# ---------------------------------------------------------------------------
# SPINE send helpers (read/call/result)
# ---------------------------------------------------------------------------


async def send_spine_read(
    ws,
    *,
    address_source: Dict[str, Any],
    address_destination: Dict[str, Any],
    cmd: Dict[str, Any],
    msg_counter: MsgCounter,
    specification_version: str = "1.3.0",
    ack_request: bool = True,
):
    """Send a SPINE read datagram to the remote device."""
    try:
        print(f"📤 [SPINE] Read send: {list(cmd.keys())}")
    except Exception:
        pass
    datagram: Dict[str, Any] = {
        "datagram": {
            "header": {
                "specificationVersion": specification_version,
                "addressSource": address_source,
                "addressDestination": address_destination,
                "msgCounter": await msg_counter.next(),
                "cmdClassifier": "read",
                "ackRequest": ack_request,
            },
            "payload": {"cmd": [cmd]},
        }
    }
    await send_ship_data(ws, datagram)


async def send_spine_call(
    ws,
    *,
    address_source: Dict[str, Any],
    address_destination: Dict[str, Any],
    cmd: Dict[str, Any],
    msg_counter: MsgCounter,
    specification_version: str = "1.3.0",
    ack_request: bool = True,
):
    """Send a SPINE call datagram to the remote device."""
    try:
        print(f"📤 [SPINE] Call send: {list(cmd.keys())}")
    except Exception:
        pass
    datagram: Dict[str, Any] = {
        "datagram": {
            "header": {
                "specificationVersion": specification_version,
                "addressSource": address_source,
                "addressDestination": address_destination,
                "msgCounter": await msg_counter.next(),
                "cmdClassifier": "call",
                "ackRequest": ack_request,
            },
            "payload": {"cmd": [cmd]},
        }
    }
    await send_ship_data(ws, datagram)


async def send_spine_result_ok(
    ws,
    *,
    request_header: Dict[str, Any],
    local_device_address: str,
    msg_counter: MsgCounter,
):
    """Send SPINE cmdClassifier='result' (errorNumber 0) acknowledging a received datagram."""
    ref = request_header.get("msgCounter")
    if ref is None:
        return

    addresses = _make_spine_reply_addresses(request_header, local_device_address=local_device_address)
    if addresses is None:
        return

    address_source, address_destination = addresses

    result_datagram: Dict[str, Any] = {
        "datagram": {
            "header": {
                "specificationVersion": request_header.get("specificationVersion", "1.3.0"),
                "addressSource": address_source,
                "addressDestination": address_destination,
                "msgCounter": await msg_counter.next(),
                "msgCounterReference": ref,
                "cmdClassifier": "result",
            },
            "payload": {"cmd": [{"resultData": {"errorNumber": 0}}]},
        }
    }

    await send_ship_data(ws, result_datagram)
    try:
        print(f"📤 [SPINE] Result send: msgCounterReference={ref}")
    except Exception:
        pass


def _extract_remote_landmap(discovery: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Return (heat_pump_entity_address, feature_type_to_feature_address)."""
    heat_pump_entity_addr: Optional[Dict[str, Any]] = None
    feature_type_to_addr: Dict[str, Dict[str, Any]] = {}

    entity_info = discovery.get("entityInformation")
    if isinstance(entity_info, list):
        for item in entity_info:
            if not isinstance(item, dict):
                continue
            desc = item.get("description")
            if not isinstance(desc, dict):
                continue
            if desc.get("entityType") == "HeatPumpAppliance":
                ent_addr = desc.get("entityAddress")
                if isinstance(ent_addr, dict):
                    heat_pump_entity_addr = ent_addr
                    break

    feature_info = discovery.get("featureInformation")
    if isinstance(feature_info, list):
        for item in feature_info:
            if not isinstance(item, dict):
                continue
            desc = item.get("description")
            if not isinstance(desc, dict):
                continue
            feature_type = desc.get("featureType")
            feature_addr = desc.get("featureAddress")
            if isinstance(feature_type, str) and isinstance(feature_addr, dict):
                feature_type_to_addr[feature_type] = feature_addr

    return heat_pump_entity_addr, feature_type_to_addr


def _entity_addr_list(entity_address: Any) -> Optional[list[int]]:
    if not isinstance(entity_address, dict):
        return None
    entity = entity_address.get("entity")
    if not isinstance(entity, list) or not entity:
        return None
    if not all(isinstance(x, int) for x in entity):
        return None
    return [int(x) for x in entity]


def _is_prefix(prefix: list[int], value: list[int]) -> bool:
    if len(prefix) > len(value):
        return False
    return value[: len(prefix)] == prefix


def _extract_entities(discovery: Dict[str, Any]) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    entity_info = discovery.get("entityInformation")
    if not isinstance(entity_info, list):
        return out
    for item in entity_info:
        if not isinstance(item, dict):
            continue
        desc = item.get("description")
        if not isinstance(desc, dict):
            continue
        ent_addr = _entity_addr_list(desc.get("entityAddress"))
        if ent_addr is None:
            continue
        out.append(
            {
                "entity": ent_addr,
                "entityType": desc.get("entityType"),
                "description": desc.get("description"),
            }
        )
    out.sort(key=lambda d: d.get("entity") or [])
    return out


def _extract_measurement_servers(discovery: Dict[str, Any]) -> list[Dict[str, Any]]:
    servers: list[Dict[str, Any]] = []
    feature_info = discovery.get("featureInformation")
    if not isinstance(feature_info, list):
        return servers
    for item in feature_info:
        if not isinstance(item, dict):
            continue
        desc = item.get("description")
        if not isinstance(desc, dict):
            continue
        if desc.get("role") != "server":
            continue
        if desc.get("featureType") != "Measurement":
            continue
        faddr = desc.get("featureAddress")
        if not isinstance(faddr, dict):
            continue
        entity = faddr.get("entity")
        feature = faddr.get("feature")
        if not isinstance(entity, list) or not entity or not all(isinstance(x, int) for x in entity):
            continue
        if not isinstance(feature, int):
            continue
        servers.append({"entity": [int(x) for x in entity], "feature": int(feature)})
    servers.sort(key=lambda d: (d.get("entity") or [], d.get("feature") or 0))
    return servers


async def reply_node_management_detailed_discovery(
    ws,
    *,
    request_header: Dict[str, Any],
    local_device_address: str,
    msg_counter: MsgCounter,
):
    """Reply to a SPINE read request for nodeManagementDetailedDiscoveryData."""
    ref = request_header.get("msgCounter")
    if ref is None:
        print("⚠️  [SPINE] Kein msgCounter im Request-Header → kann nicht antworten")
        return

    addresses = _make_spine_reply_addresses(request_header, local_device_address=local_device_address)
    if addresses is None:
        print("⚠️  [SPINE] Request ohne addressSource/addressDestination → kann nicht antworten")
        return

    address_source, address_destination = addresses

    reply_datagram: Dict[str, Any] = {
        "datagram": {
            "header": {
                "specificationVersion": request_header.get("specificationVersion", "1.3.0"),
                "addressSource": address_source,
                "addressDestination": address_destination,
                "msgCounter": await msg_counter.next(),
                "msgCounterReference": ref,
                "cmdClassifier": "reply",
            },
            "payload": {
                "cmd": [
                    {
                        "nodeManagementDetailedDiscoveryData": build_local_detailed_discovery(local_device_address),
                        "function": "nodeManagementDetailedDiscoveryData",
                    }
                ]
            },
        }
    }

    await send_ship_data(ws, reply_datagram)
    print("📤 [SPINE] Reply gesendet: nodeManagementDetailedDiscoveryData")


async def reply_device_classification_manufacturer_data(
    ws,
    *,
    request_header: Dict[str, Any],
    local_device_address: str,
    msg_counter: MsgCounter,
):
    ref = request_header.get("msgCounter")
    if ref is None:
        return

    addresses = _make_spine_reply_addresses(request_header, local_device_address=local_device_address)
    if addresses is None:
        return
    address_source, address_destination = addresses

    reply_datagram: Dict[str, Any] = {
        "datagram": {
            "header": {
                "specificationVersion": request_header.get("specificationVersion", "1.3.0"),
                "addressSource": address_source,
                "addressDestination": address_destination,
                "msgCounter": await msg_counter.next(),
                "msgCounterReference": ref,
                "cmdClassifier": "reply",
            },
            "payload": {
                "cmd": [
                    {
                        "deviceClassificationManufacturerData": build_device_classification_manufacturer_data(
                            local_device_address
                        )
                    }
                ]
            },
        }
    }

    await send_ship_data(ws, reply_datagram)
    print("📤 [SPINE] Reply gesendet: deviceClassificationManufacturerData")


async def reply_device_classification_user_data(
    ws,
    *,
    request_header: Dict[str, Any],
    local_device_address: str,
    msg_counter: MsgCounter,
):
    ref = request_header.get("msgCounter")
    if ref is None:
        return

    addresses = _make_spine_reply_addresses(request_header, local_device_address=local_device_address)
    if addresses is None:
        return
    address_source, address_destination = addresses

    reply_datagram: Dict[str, Any] = {
        "datagram": {
            "header": {
                "specificationVersion": request_header.get("specificationVersion", "1.3.0"),
                "addressSource": address_source,
                "addressDestination": address_destination,
                "msgCounter": await msg_counter.next(),
                "msgCounterReference": ref,
                "cmdClassifier": "reply",
            },
            "payload": {"cmd": [{"deviceClassificationUserData": build_device_classification_user_data()}]},
        }
    }

    await send_ship_data(ws, reply_datagram)
    print("📤 [SPINE] Reply gesendet: deviceClassificationUserData")


async def handle_spine_read(
    ws,
    *,
    request_header: Dict[str, Any],
    cmd: Dict[str, Any],
    local_device_address: str,
    msg_counter: MsgCounter,
):
    """Handle SPINE cmdClassifier='read' with minimal required replies."""
    if "nodeManagementDetailedDiscoveryData" in cmd:
        await reply_node_management_detailed_discovery(
            ws,
            request_header=request_header,
            local_device_address=local_device_address,
            msg_counter=msg_counter,
        )
        return

    if "deviceClassificationManufacturerData" in cmd:
        await reply_device_classification_manufacturer_data(
            ws,
            request_header=request_header,
            local_device_address=local_device_address,
            msg_counter=msg_counter,
        )
        return

    if "deviceClassificationUserData" in cmd:
        await reply_device_classification_user_data(
            ws,
            request_header=request_header,
            local_device_address=local_device_address,
            msg_counter=msg_counter,
        )
        return

    print(f"⚠️  [SPINE] Unhandled read cmd keys: {list(cmd.keys())}")


async def request_remote_detailed_discovery(
    ws,
    *,
    local_device_address: str,
    remote_device_address: str,
    msg_counter: MsgCounter,
):
    src = _spine_addr(device=local_device_address, entity=0, feature=0)
    dst = _spine_addr(device=remote_device_address, entity=0, feature=0)
    await send_spine_read(
        ws,
        address_source=src,
        address_destination=dst,
        cmd={"nodeManagementDetailedDiscoveryData": {}},
        msg_counter=msg_counter,
    )
    print("📤 [SPINE] Read gesendet: nodeManagementDetailedDiscoveryData")


async def request_remote_node_management_use_case_data(
    ws,
    *,
    local_device_address: str,
    remote_device_address: str,
    msg_counter: MsgCounter,
):
    # Per user requirement: NodeManagement (Entity 0, Feature 0), function nodeManagementUseCaseData
    src = _spine_addr(device=local_device_address, entity=0, feature=0)
    dst = _spine_addr(device=remote_device_address, entity=0, feature=0)
    await send_spine_read(
        ws,
        address_source=src,
        address_destination=dst,
        cmd={"nodeManagementUseCaseData": {}},
        msg_counter=msg_counter,
    )
    print("📤 [SPINE] Read gesendet: nodeManagementUseCaseData")


async def request_remote_measurement_once(
    ws,
    *,
    local_device_address: str,
    remote_device_address: str,
    remote_measurement_feature: Dict[str, Any],
    msg_counter: MsgCounter,
):
    entity_list = remote_measurement_feature.get("entity")
    feature = remote_measurement_feature.get("feature")
    if not isinstance(entity_list, list) or not entity_list or not all(isinstance(x, int) for x in entity_list):
        return
    if not isinstance(feature, int):
        return

    # Use our Measurement client feature as source.
    src = _spine_addr(device=local_device_address, entity=1, feature=1)
    dst = {"device": remote_device_address, "entity": [int(x) for x in entity_list], "feature": int(feature)}

    await send_spine_read(
        ws,
        address_source=src,
        address_destination=dst,
        cmd={"measurementDescriptionListData": {}},
        msg_counter=msg_counter,
        ack_request=True,
    )
    await send_spine_read(
        ws,
        address_source=src,
        address_destination=dst,
        cmd={"measurementListData": {}},
        msg_counter=msg_counter,
        ack_request=True,
    )


async def subscribe_remote_measurement(
    ws,
    *,
    local_device_address: str,
    remote_device_address: str,
    remote_measurement_feature: Dict[str, Any],
    msg_counter: MsgCounter,
):
    entity_list = remote_measurement_feature.get("entity")
    feature = remote_measurement_feature.get("feature")
    if not isinstance(entity_list, list) or not entity_list or not all(isinstance(x, int) for x in entity_list):
        return
    if not isinstance(feature, int):
        return

    # NodeManagement call sets up subscriptions.
    src_nm = _spine_addr(device=local_device_address, entity=0, feature=0)
    dst_nm = _spine_addr(device=remote_device_address, entity=0, feature=0)
    local_meas_client = _spine_addr(device=local_device_address, entity=1, feature=1)
    remote_meas_server = {"device": remote_device_address, "entity": [int(x) for x in entity_list], "feature": int(feature)}

    sub_call = {
        "subscriptionRequest": {
            "clientAddress": local_meas_client,
            "serverAddress": remote_meas_server,
            "serverFeatureType": "Measurement",
        }
    }
    await send_spine_call(
        ws,
        address_source=src_nm,
        address_destination=dst_nm,
        cmd={"nodeManagementSubscriptionRequestCall": sub_call},
        msg_counter=msg_counter,
        ack_request=True,
    )


def parse_measurement_description(cmd: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """Parse measurementDescriptionListData and return {measurementId: {scopeType, unit, measurementType}}.

    Vaillant/VR921 commonly returns this as:
      {"measurementDescriptionListData": [ {measurementId, scopeType, unit, ...}, ... ]}
    (i.e. the value is a list directly).
    """

    desc_map: Dict[int, Dict[str, Any]] = {}

    mdl = cmd.get("measurementDescriptionListData")
    mdl_list: Optional[list] = None

    if isinstance(mdl, list):
        mdl_list = mdl
    elif isinstance(mdl, dict):
        # Fallbacks for alternate nesting seen in other stacks.
        if isinstance(mdl.get("measurementDescriptionListData"), list):
            mdl_list = cast(list, mdl.get("measurementDescriptionListData"))
        elif isinstance(mdl.get("measurementDescriptionData"), list):
            mdl_list = cast(list, mdl.get("measurementDescriptionData"))

    if not isinstance(mdl_list, list):
        try:
            print(
                f"⚠️  [MEASUREMENT] measurementDescriptionListData unexpected type: {type(mdl).__name__} keys={list(mdl.keys()) if isinstance(mdl, dict) else ''}"
            )
        except Exception:
            pass
        return desc_map

    for entry in mdl_list:
        if not isinstance(entry, dict):
            continue
        mid = entry.get("measurementId")
        if not isinstance(mid, int):
            continue
        desc_map[mid] = {
            "scopeType": entry.get("scopeType"),
            "unit": entry.get("unit"),
            "measurementType": entry.get("measurementType"),
        }

    return desc_map


def parse_measurement_list(
    cmd: Dict[str, Any],
    desc_map: Dict[int, Dict[str, Any]],
    *,
    source_address: Optional[Dict[str, Any]] = None,
) -> list[Dict[str, Any]]:
    """Parse measurementListData and return structured updates.

    Vaillant/VR921 commonly returns this as:
      {"measurementListData": [ {measurementId, measurementData:{value:{number,scale}}}, ... ]}
    """

    def _scaled_number_to_float(v: Any) -> Optional[float]:
        if not isinstance(v, dict):
            return None
        number = v.get("number")
        scale = v.get("scale", 0)
        if not isinstance(number, int):
            return None
        if not isinstance(scale, int):
            scale = 0
        try:
            return float(number) * (10.0 ** float(scale))
        except Exception:
            return None

    ml = cmd.get("measurementListData")
    ml_list: Optional[list] = None

    if isinstance(ml, list):
        ml_list = ml
    elif isinstance(ml, dict):
        # Fallback for alternate nesting.
        if isinstance(ml.get("measurementListData"), list):
            ml_list = cast(list, ml.get("measurementListData"))
        elif isinstance(ml.get("measurementData"), list):
            ml_list = cast(list, ml.get("measurementData"))

    if not isinstance(ml_list, list):
        try:
            print(
                f"⚠️  [MEASUREMENT] measurementListData unexpected type: {type(ml).__name__} keys={list(ml.keys()) if isinstance(ml, dict) else ''}"
            )
        except Exception:
            pass
        return []

    updates: list[Dict[str, Any]] = []
    any_printed = False

    src_entity: Optional[list[int]] = None
    src_feature: Optional[int] = None
    if isinstance(source_address, dict):
        ent = source_address.get("entity")
        feat = source_address.get("feature")
        if isinstance(ent, list) and all(isinstance(x, int) for x in ent):
            src_entity = [int(x) for x in ent]
        if isinstance(feat, int):
            src_feature = int(feat)

    for entry in ml_list:
        if not isinstance(entry, dict):
            continue

        mid = entry.get("measurementId")
        mdata = entry.get("measurementData")
        if not isinstance(mid, int):
            continue

        # Prefer the canonical shape: entry.measurementData.value (scaledNumber)
        val = None
        if isinstance(mdata, dict):
            val = _scaled_number_to_float(mdata.get("value"))
        # Fallbacks: some devices may inline value
        if val is None:
            val = _scaled_number_to_float(entry.get("value"))
        if val is None:
            continue

        meta = desc_map.get(mid, {})
        scope = meta.get("scopeType") or "unknown"
        unit = meta.get("unit") or ""
        mtype = meta.get("measurementType") or ""

        # unit can be string/enum-like; keep it printable
        if isinstance(unit, dict):
            unit = unit.get("unit") or unit.get("name") or json.dumps(unit, separators=(",", ":"), ensure_ascii=False)

        scope_str = scope if isinstance(scope, str) else str(scope)
        unit_str = unit if isinstance(unit, str) else str(unit)
        mtype_str = mtype if isinstance(mtype, str) else str(mtype)

        updates.append(
            {
                "measurementId": mid,
                "scopeType": scope_str,
                "unit": unit_str,
                "measurementType": mtype_str,
                "value": val,
                "source": {"entity": src_entity, "feature": src_feature},
            }
        )

        # Keep human output helpful but short
        if isinstance(scope_str, str) and ("outdoortemperature" in scope_str.lower()):
            print(f"🌡️  Außentemperatur: {val} {unit_str} (ID {mid})")
            any_printed = True
        elif isinstance(scope_str, str) and ("dhwtemperature" in scope_str.lower()):
            print(f"🚿 DHW: {val} {unit_str} (ID {mid})")
            any_printed = True
        elif isinstance(scope_str, str) and (
            "acpowertotal" in scope_str.lower() or "power" == scope_str.lower() or "power" in scope_str.lower()
        ):
            print(f"⚡ Leistung: {val} {unit_str} (ID {mid}, scope={scope_str})")
            any_printed = True
        else:
            print(f"📊 Measurement: {val} {unit_str} (scope={scope_str}, ID {mid})")
            any_printed = True

    if not any_printed:
        # If we got here, list existed but no usable values were found.
        try:
            sample = json.dumps(ml_list[:3], indent=2, ensure_ascii=False)
            print(f"⚠️  [MEASUREMENT] No values parsed; sample entries:\n{sample}")
        except Exception:
            pass

    return updates


# ---------------------------------------------------------------------------
# SHIP handshake
# ---------------------------------------------------------------------------


async def perform_ship_handshake(ws, local_ship_id: str):
    """
    SHIP Handshake nach offizieller Spezifikation:
    Phase 1: CMI (Connection Mode Init) - Initial-Byte
    Phase 2: Hello - Trust establishment
    Phase 3: Protocol - Version negotiation
    Phase 4: PIN - Authentication (nur "none" unterstützt)
    Phase 5: Access - Access methods exchange
    """
    
    # === PHASE 1: CMI (Connection Mode Init) ===
    init_ack = await ws.recv()
    if isinstance(init_ack, bytes):
        print(f"📥 [CMI] Initial-Bytes empfangen: {init_ack.hex()}")
    else:
        print(f"📥 [CMI] Initial empfangen (nicht-bytes): {init_ack}")
    
    # === PHASE 2: HELLO ===
    print("📤 [HELLO] Sende connectionHello (phase: ready)...")
    await send_ship_json(ws, {"connectionHello": {"phase": "ready", "waiting": 60000}})

    # State machine for the SHIP handshake phases.
    # We deliberately do not "jump ahead" while the peer is still pending (waiting
    # for the user to press Trust in the myVAILLANT app).
    state = "WAITING_HELLO"
    protocol_handshake_received = False
    pin_state_received = False
    last_pending_hello_sent = 0.0
    
    while True:
        try:
            raw_msg = await ws.recv()
        except Exception as e:
            # websockets raises ConnectionClosedError/OK subclasses
            code = getattr(e, "code", None)
            reason = getattr(e, "reason", None)
            print(f"❌ WebSocket geschlossen während Handshake: code={code} reason={reason} err={e}")
            return False
        if not isinstance(raw_msg, bytes) or len(raw_msg) < 2:
            continue
        
        # SHIP control frames use message type 0x01.
        # During/after handshake we might also see data frames (0x02).
        header = raw_msg[0]
        if header != 0x01:
            # During/after handshake we might also see data messages (0x02)
            print(f"⚠️  Unerwarteter SHIP MessageType: 0x{header:02x} (len={len(raw_msg)})")
            continue

        payload_raw = raw_msg[1:]
        payload_text = payload_raw.decode("utf-8", errors="ignore")
        payload_text = json_from_eebus_json(payload_text)
        
        try:
            msg = json.loads(payload_text)
            print(f"📥 Empfangen: {json.dumps(msg, indent=2)}")
        except json.JSONDecodeError:
            print(f"⚠️  JSON Decode Error: {payload_text[:200]}")
            continue

        # === PHASE 2: HELLO RESPONSE ===
        if "connectionHello" in msg and state == "WAITING_HELLO":
            hello = msg.get("connectionHello") or {}
            phase = hello.get("phase")
            
            if phase == "pending":
                prolong = hello.get("prolongationRequest")
                waiting_ms = hello.get("waiting")

                print("⏳ [HELLO] STATUS: PENDING - Warte auf Bestätigung in der myVAILLANT App...")
                print("👉 JETZT in der App den Zugriff bestätigen!")

                # Important: while the remote side is still pending (waiting for user trust/pairing),
                # we MUST NOT proceed to protocol/pin/access. To keep the hello phase alive and
                # avoid timeouts, we answer with our own PENDING + waiting.
                if isinstance(waiting_ms, int):
                    print(f"⏳ [HELLO] Remote waiting={waiting_ms}ms prolongationRequest={prolong}")
                else:
                    print(f"⏳ [HELLO] Remote prolongationRequest={prolong}")

                now = time.monotonic()
                if now - last_pending_hello_sent > 5.0:
                    await send_ship_json(ws, {"connectionHello": {"phase": "pending", "waiting": 60000}})
                    last_pending_hello_sent = now
                # Bleibe in WAITING_HELLO State
                
            elif phase == "ready":
                print("✅ [HELLO] Phase abgeschlossen - beide Seiten READY")
                
                # === PHASE 3: PROTOCOL HANDSHAKE ===
                print("📤 [PROTOCOL] Sende messageProtocolHandshake...")
                await send_ship_json(
                    ws,
                    {
                        "messageProtocolHandshake": {
                            "handshakeType": "announceMax",
                            "version": {"major": 1, "minor": 0},
                            "formats": {"format": ["JSON-UTF8"]},
                        }
                    },
                )
                state = "WAITING_PROTOCOL"
                
            elif phase == "aborted":
                print("❌ [HELLO] Verbindung von Wärmepumpe abgelehnt (Aborted).")
                return False

        # === PHASE 3: PROTOCOL RESPONSE ===
        elif "messageProtocolHandshake" in msg and state == "WAITING_PROTOCOL":
            handshake = msg.get("messageProtocolHandshake") or {}

            # ship-go client behavior: remote replies with "select" -> client must send "select" confirmation
            handshake_type = handshake.get("handshakeType")
            if handshake_type != "select":
                print(f"❌ [PROTOCOL] Unerwarteter handshakeType: {handshake_type}")
                return False
            
            # Validiere Protokoll-Version
            version = handshake.get("version", {})
            if version.get("major") != 1:
                print(f"❌ [PROTOCOL] Nicht unterstützte Version: {version}")
                return False
            
            print("✅ [PROTOCOL] Protokoll-Handshake bestätigt (Version 1.0)")
            protocol_handshake_received = True

            print("📤 [PROTOCOL] Bestätige Auswahl (select)...")
            await send_ship_json(
                ws,
                {
                    "messageProtocolHandshake": {
                        "handshakeType": "select",
                        "version": {"major": 1, "minor": 0},
                        "formats": {"format": ["JSON-UTF8"]},
                    }
                },
            )
            
            # === PHASE 4: PIN STATE ===
            print("📤 [PIN] Sende connectionPinState (none)...")
            await send_ship_json(ws, {"connectionPinState": {"pinState": "none"}})
            state = "WAITING_PIN"

        elif "messageProtocolHandshakeError" in msg:
            err = (msg.get("messageProtocolHandshakeError") or {}).get("error")
            print(f"❌ [PROTOCOL] messageProtocolHandshakeError empfangen: error={err}")
            return False
            
        # === PHASE 4: PIN RESPONSE (Optional - manche Geräte bestätigen, manche nicht) ===
        elif "connectionPinState" in msg and state == "WAITING_PIN":
            pin_state = (msg.get("connectionPinState") or {}).get("pinState")
            print(f"✅ [PIN] PIN-State bestätigt: {pin_state}")
            if pin_state != "none":
                print("❌ [PIN] Gerät verlangt PIN (oder sendet unerwarteten Zustand). ship-go unterstützt nur 'none'.")
                return False

            pin_state_received = True

            # === PHASE 5: ACCESS METHODS ===
            print("📤 [ACCESS] Sende accessMethodsRequest...")
            await send_ship_json(ws, {"accessMethodsRequest": {}})
            state = "WAITING_ACCESS"
            
        # In ship-go ist PIN eine eigene Phase; ohne PIN-State nicht zur Access-Phase springen.

        # === PHASE 5: ACCESS RESPONSE ===
        elif "accessMethodsRequest" in msg and state == "WAITING_ACCESS":
            print("📥 [ACCESS] accessMethodsRequest vom Gerät empfangen → sende accessMethods...")
            await send_access_methods(ws, local_ship_id)
            # Stay in WAITING_ACCESS until we receive accessMethods

        elif "accessMethods" in msg and state == "WAITING_ACCESS":
            remote_id = (msg.get("accessMethods") or {}).get("id", "unknown")
            print(f"✅ [ACCESS] Access Methods empfangen (Remote ID: {remote_id})")
            print("")
            print("="*60)
            print("💎 SHIP HANDSHAKE ERFOLGREICH BEENDET!")
            print("="*60)
            print("")
            return True
        
        elif state == "WAITING_ACCESS" and "connectionPinState" not in msg and "accessMethods" not in msg and "accessMethodsRequest" not in msg:
            # Falls wir im ACCESS-State sind, aber die falsche Nachricht kommt
            print(f"⚠️  [STATE: {state}] Unerwartete Nachricht: {list(msg.keys())}")

async def main():
    """Entry point.

    Does:
    - Create/reuse client certificate (SKI)
    - mDNS announce + discover VR921
    - Connect websocket + perform SHIP handshake
    - Receive SPINE traffic, answer required reads, then subscribe/read measurements
    - Optionally publish Home Assistant MQTT discovery + states
    """
    print("🚀 EEBUS SHIP Client gestartet")
    # Stable client identity for trust/pairing.
    my_ski = get_or_create_certificate()
    local_ship_id = f"python-{my_ski[:12]}"
    local_device_address = f"d:_i:1_{local_ship_id}"
    msg_counter = MsgCounter(start=1)

    ha_device_id = _slug(_env_str("HA_DEVICE_ID", f"eebus_{local_ship_id}"))
    ha_device_name = _env_str("HA_DEVICE_NAME", "EEBUS HeatPump")
    mqtt_pub = HAMqttPublisher(device_id=ha_device_id, device_name=ha_device_name)
    
    # Determine our outbound IPv4 address to advertise via mDNS.
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    local_ip = s.getsockname()[0]
    s.close()

    aiozc = AsyncZeroconf(ip_version=IPVersion.V4Only)
    handler = MDNSHandler(my_ski)
    
    # Advertise our local SHIP service. This helps the app/device to "see" this client.
    desc = {'txtvers': '1', 'path': '/ship/', 'ski': my_ski, 'register': 'true'}
    info = AsyncServiceInfo("_ship._tcp.local.", f"Python-{my_ski[:6]}._ship._tcp.local.",
                            addresses=[socket.inet_aton(local_ip)], port=54885, properties=desc)
    
    await aiozc.async_register_service(info)
    AsyncServiceBrowser(aiozc.zeroconf, "_ship._tcp.local.", handler)
    
    print(f"📢 mDNS aktiv: {local_ip}")
    print("🔎 Suche nach VR921 (mDNS _ship._tcp.local.)...")
    
    timeout = 30
    elapsed = 0
    while handler.target_info is None and elapsed < timeout:
        await asyncio.sleep(1)
        elapsed += 1
    
    if handler.target_info is None:
        print("❌ Kein VR921 gefunden.")
        return
    
    target_ip = socket.inet_ntoa(handler.target_info.addresses[0])
    target_port = handler.target_info.port
    target_ski = handler.target_info.properties.get(b'ski', b'unknown').decode('utf-8')
    print(f"✅ VR921 gefunden: {target_ip}:{target_port}")
    print(f"   Remote SKI: {target_ski}")

    # TLS context: we authenticate with our client cert; we do not validate the gateway cert.
    ssl_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_ctx.load_cert_chain(certfile="cert.pem", keyfile="key.pem")
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    ssl_ctx.set_ciphers('HIGH:!aNULL:!eNULL:!MD5@SECLEVEL=1')

    try:
        import websockets
        print(f"🔌 Verbinde WebSocket...")
        # Connect to the gateway SHIP websocket endpoint.
        async with websockets.connect(f"wss://{target_ip}:{target_port}/ship/", 
                                     ssl=ssl_ctx, subprotocols=cast(Any, ["ship"]), open_timeout=20) as ws:
            await ws.send(b'\x00\x00')
            
            success = await perform_ship_handshake(ws, local_ship_id)
            if not success:
                print("❌ Handshake fehlgeschlagen")
                return
            
            print("🎯 SHIP Layer erfolgreich!")
            print("📡 Warte auf SPINE-Daten oder sende eigene Anfragen...")
            print("")

            # Optional: publish values for Home Assistant via MQTT discovery
            mqtt_pub.connect()
            publish_jsonl = _env_bool("SHIP_JSONL", False)
            discovery_log = _env_bool("SHIP_DISCOVERY_LOG", True)
            
            # Main receive loop:
            # - parse SHIP Control (0x01) and SHIP Data (0x02)
            # - for SPINE data: ACK with result, reply to required READs
            # - after discovery/usecase: subscribe/read measurement servers
            message_count = 0
            last_control_msg: Optional[Dict[str, Any]] = None
            last_spine_header: Optional[Dict[str, Any]] = None
            last_spine_cmd: Optional[Dict[str, Any]] = None
            remote_device_address: Optional[str] = None
            discovery_requested = False
            remote_feature_map: Dict[str, Dict[str, Any]] = {}
            peer_use_case_received = False
            measurement_subscription_sent = False
            measurement_read_sent = False
            # measurement IDs are server-local (IDs overlap across different entities/features).
            # Keep description maps keyed by the source feature address to resolve notify updates correctly.
            measurement_desc_maps: Dict[tuple[tuple[int, ...], int], Dict[int, Dict[str, Any]]] = {}
            latest_measurements: Dict[str, Dict[str, Any]] = {}
            ha_published: set[str] = set()
            remote_entities: list[Dict[str, Any]] = []
            remote_measurement_servers: list[Dict[str, Any]] = []
            selected_measurement_servers: list[Dict[str, Any]] = []

            def _desc_key_from_address(addr: Any) -> Optional[tuple[tuple[int, ...], int]]:
                if not isinstance(addr, dict):
                    return None
                ent = addr.get("entity")
                feat = addr.get("feature")
                if not (isinstance(ent, list) and ent and all(isinstance(x, int) for x in ent)):
                    return None
                if not isinstance(feat, int):
                    return None
                return (tuple(int(x) for x in ent), int(feat))

            try:
                while True:
                    try:
                        data = await asyncio.wait_for(ws.recv(), timeout=60)
                    except asyncio.TimeoutError:
                        # SHIP keep-alive: periodically signal readiness while idle.
                        try:
                            await send_ship_json(ws, {"connectionHello": {"phase": "ready", "waiting": 60000}})
                            print("📤 [HELLO] Keep-alive gesendet (ready)")
                        except Exception as e:
                            print(f"⚠️  [HELLO] Keep-alive fehlgeschlagen: {e}")
                        continue
                    message_count += 1
                    
                    # Header-Byte prüfen und JSON parsen
                    if isinstance(data, bytes) and len(data) > 0:
                        if data[0] == 1:  # SHIP Control
                            try:
                                payload_text = data[1:].decode("utf-8", errors="ignore")
                                payload_text = json_from_eebus_json(payload_text)
                                msg = json.loads(payload_text)
                                if isinstance(msg, dict):
                                    last_control_msg = msg
                                print(f"\n📨 Nachricht #{message_count}:")
                                print(json.dumps(msg, indent=2))
                            except:
                                print(f"\n📨 Nachricht #{message_count} (Binär): {data.hex()}")
                        elif data[0] == 2:  # SHIP Data (SPINE)
                            try:
                                payload_text = data[1:].decode("utf-8", errors="ignore")
                                payload_text = json_from_eebus_json(payload_text)
                                msg = json.loads(payload_text)

                                parsed = _parse_spine_datagram(msg)
                                if parsed is None:
                                    print(f"\n📨 SPINE #{message_count} (unparsed):")
                                    print(json.dumps(msg, indent=2)[:5000])
                                    continue

                                hdr, cmd = parsed
                                last_spine_header = hdr
                                last_spine_cmd = cmd
                                cmd_classifier = hdr.get("cmdClassifier")
                                ack_req = hdr.get("ackRequest")

                                # Learn remote device address from any SPINE traffic.
                                if remote_device_address is None:
                                    addr_src = hdr.get("addressSource")
                                    if isinstance(addr_src, dict):
                                        dev = addr_src.get("device")
                                        if isinstance(dev, str) and dev:
                                            remote_device_address = dev

                                # Once we know the remote device address, request its detailed discovery.
                                if remote_device_address is not None and not discovery_requested:
                                    discovery_requested = True
                                    await request_remote_detailed_discovery(
                                        ws,
                                        local_device_address=local_device_address,
                                        remote_device_address=remote_device_address,
                                        msg_counter=msg_counter,
                                    )

                                print(f"\n📨 SPINE #{message_count}: cmdClassifier={cmd_classifier} ackRequest={ack_req}")

                                try:
                                    # ACK every received datagram with a SPINE 'result' (except results themselves).
                                    if cmd_classifier != "result":
                                        await send_spine_result_ok(
                                            ws,
                                            request_header=hdr,
                                            local_device_address=local_device_address,
                                            msg_counter=msg_counter,
                                        )

                                    if cmd_classifier == "read":
                                        await handle_spine_read(
                                            ws,
                                            request_header=hdr,
                                            cmd=cmd,
                                            local_device_address=local_device_address,
                                            msg_counter=msg_counter,
                                        )
                                    elif cmd_classifier == "reply":
                                        # This is where the remote sends its internal map.
                                        if "nodeManagementDetailedDiscoveryData" in cmd:
                                            discovery = cmd.get("nodeManagementDetailedDiscoveryData")
                                            if isinstance(discovery, dict):
                                                hp_entity, feature_map = _extract_remote_landmap(discovery)
                                                remote_feature_map.update(feature_map)

                                                remote_entities = _extract_entities(discovery)
                                                hp_prefix = _entity_addr_list(hp_entity) if isinstance(hp_entity, dict) else None
                                                if hp_prefix is not None:
                                                    print(f"✅ [DISCOVERY] entityType=HeatPumpAppliance at entity={hp_prefix}")
                                                    print("📌 [DISCOVERY] HeatPump Entities (prefix match):")
                                                    any_hp = False
                                                    for e in remote_entities:
                                                        ent = e.get("entity")
                                                        if isinstance(ent, list) and all(isinstance(x, int) for x in ent) and _is_prefix(hp_prefix, cast(list[int], ent)):
                                                            any_hp = True
                                                            print(
                                                                f"  entity={ent} type={e.get('entityType')} desc={e.get('description')}"
                                                            )
                                                    if not any_hp:
                                                        print("  (keine untergeordneten Entities gefunden)")
                                                else:
                                                    print("⚠️  [DISCOVERY] HeatPumpAppliance entity not found")

                                                # Always print a compact list of all entities too.
                                                print("📌 [DISCOVERY] All Entities:")
                                                for e in remote_entities:
                                                    print(
                                                        f"  entity={e.get('entity')} type={e.get('entityType')} desc={e.get('description')}"
                                                    )

                                                remote_measurement_servers = _extract_measurement_servers(discovery)

                                                # Auto-subscribe: select all discovered Measurement servers.
                                                if remote_measurement_servers:
                                                    selected_measurement_servers = list(remote_measurement_servers)
                                                    if discovery_log:
                                                        print(
                                                            "✅ [DISCOVERY] Auto-selected all Measurement servers: "
                                                            + ", ".join(
                                                                f"entity={s.get('entity')} feature={s.get('feature')}"
                                                                for s in selected_measurement_servers
                                                            )
                                                        )

                                                # Immediately request NodeManagement UseCase data after discovery.
                                                if remote_device_address is not None:
                                                    await request_remote_node_management_use_case_data(
                                                        ws,
                                                        local_device_address=local_device_address,
                                                        remote_device_address=remote_device_address,
                                                        msg_counter=msg_counter,
                                                    )
                                        elif "nodeManagementUseCaseData" in cmd:
                                            print("✅ [USECASE] nodeManagementUseCaseData reply erhalten")
                                            peer_use_case_received = True
                                            # If discovery already found measurement servers, ensure selection is set.
                                            if not selected_measurement_servers and remote_measurement_servers:
                                                selected_measurement_servers = list(remote_measurement_servers)
                                        elif "measurementDescriptionListData" in cmd:
                                            print("✅ [MEASUREMENT] measurementDescriptionListData reply erhalten")
                                            desc_map = parse_measurement_description(cmd)
                                            key = _desc_key_from_address(hdr.get("addressSource"))
                                            if key is not None and desc_map:
                                                measurement_desc_maps[key] = desc_map
                                            print(f"   Parsed {len(desc_map)} measurement descriptions")
                                            if desc_map:
                                                print("📌 [MEASUREMENT] Description map (by ID):")
                                                for mid in sorted(desc_map.keys()):
                                                    meta = desc_map.get(mid) or {}
                                                    scope = meta.get("scopeType")
                                                    unit = meta.get("unit")
                                                    mtype = meta.get("measurementType")
                                                    if isinstance(unit, dict):
                                                        unit = unit.get("unit") or unit.get("name") or json.dumps(
                                                            unit, separators=(",", ":"), ensure_ascii=False
                                                        )
                                                    scope_str = scope if isinstance(scope, str) else str(scope)
                                                    unit_str = unit if isinstance(unit, str) else str(unit)
                                                    mtype_str = mtype if isinstance(mtype, str) else str(mtype)
                                                    print(f"  ID {mid}: scope={scope_str} unit={unit_str} type={mtype_str}")
                                        elif "measurementListData" in cmd:
                                            print("✅ [MEASUREMENT] measurementListData reply erhalten")
                                            key = _desc_key_from_address(hdr.get("addressSource"))
                                            desc_map = measurement_desc_maps.get(key, {}) if key is not None else {}
                                            updates = parse_measurement_list(
                                                cmd,
                                                desc_map,
                                                source_address=hdr.get("addressSource") if isinstance(hdr, dict) else None,
                                            )

                                            for u in updates:
                                                scope = str(u.get("scopeType") or "unknown")
                                                unit = _unit_to_ha(u.get("unit"))
                                                mid = u.get("measurementId")
                                                src = u.get("source") if isinstance(u.get("source"), dict) else {}
                                                ent = src.get("entity") if isinstance(src, dict) else None
                                                feat = src.get("feature") if isinstance(src, dict) else None

                                                object_id = _slug(
                                                    f"{scope}_e{'_'.join(str(x) for x in ent) if isinstance(ent, list) else 'na'}_f{feat if isinstance(feat, int) else 'na'}_id{mid}"
                                                )

                                                latest_measurements[object_id] = {
                                                    "value": u.get("value"),
                                                    "unit": unit,
                                                    "scopeType": scope,
                                                    "measurementId": mid,
                                                    "source": src,
                                                    "ts": time.time(),
                                                }

                                                if publish_jsonl:
                                                    print(
                                                        json.dumps(
                                                            {"type": "measurement", "object_id": object_id, **latest_measurements[object_id]},
                                                            ensure_ascii=False,
                                                            separators=(",", ":"),
                                                        )
                                                    )

                                                if isinstance(u.get("value"), (int, float)):
                                                    meta = _guess_ha_metadata(scope, unit)
                                                    if object_id not in ha_published:
                                                        friendly_name = _friendly_sensor_name(
                                                            scope,
                                                            source_entity=ent if isinstance(ent, list) else None,
                                                        )
                                                        mqtt_pub.ensure_discovery(
                                                            object_id=object_id,
                                                            name=friendly_name,
                                                            unit=meta.get("unit", unit),
                                                            device_class=meta.get("device_class", ""),
                                                            state_class=meta.get("state_class", "measurement"),
                                                        )
                                                        ha_published.add(object_id)
                                                    mqtt_pub.publish_state(object_id=object_id, value=float(u["value"]))

                                    elif cmd_classifier == "notify":
                                        # Notify payloads (push updates) are handled similar to reply.
                                        if "measurementDescriptionListData" in cmd:
                                            print("✅ [MEASUREMENT] measurementDescriptionListData notify erhalten")
                                            desc_map = parse_measurement_description(cmd)
                                            key = _desc_key_from_address(hdr.get("addressSource"))
                                            if key is not None and desc_map:
                                                measurement_desc_maps[key] = desc_map
                                            print(f"   Parsed {len(desc_map)} measurement descriptions")
                                        elif "measurementListData" in cmd:
                                            print("✅ [MEASUREMENT] measurementListData notify erhalten")
                                            key = _desc_key_from_address(hdr.get("addressSource"))
                                            desc_map = measurement_desc_maps.get(key, {}) if key is not None else {}
                                            updates = parse_measurement_list(
                                                cmd,
                                                desc_map,
                                                source_address=hdr.get("addressSource") if isinstance(hdr, dict) else None,
                                            )

                                            for u in updates:
                                                scope = str(u.get("scopeType") or "unknown")
                                                unit = _unit_to_ha(u.get("unit"))
                                                mid = u.get("measurementId")
                                                src = u.get("source") if isinstance(u.get("source"), dict) else {}
                                                ent = src.get("entity") if isinstance(src, dict) else None
                                                feat = src.get("feature") if isinstance(src, dict) else None

                                                object_id = _slug(
                                                    f"{scope}_e{'_'.join(str(x) for x in ent) if isinstance(ent, list) else 'na'}_f{feat if isinstance(feat, int) else 'na'}_id{mid}"
                                                )

                                                latest_measurements[object_id] = {
                                                    "value": u.get("value"),
                                                    "unit": unit,
                                                    "scopeType": scope,
                                                    "measurementId": mid,
                                                    "source": src,
                                                    "ts": time.time(),
                                                }

                                                if publish_jsonl:
                                                    print(
                                                        json.dumps(
                                                            {"type": "measurement", "object_id": object_id, **latest_measurements[object_id]},
                                                            ensure_ascii=False,
                                                            separators=(",", ":"),
                                                        )
                                                    )

                                                if isinstance(u.get("value"), (int, float)):
                                                    meta = _guess_ha_metadata(scope, unit)
                                                    if object_id not in ha_published:
                                                        friendly_name = _friendly_sensor_name(
                                                            scope,
                                                            source_entity=ent if isinstance(ent, list) else None,
                                                        )
                                                        mqtt_pub.ensure_discovery(
                                                            object_id=object_id,
                                                            name=friendly_name,
                                                            unit=meta.get("unit", unit),
                                                            device_class=meta.get("device_class", ""),
                                                            state_class=meta.get("state_class", "measurement"),
                                                        )
                                                        ha_published.add(object_id)
                                                    mqtt_pub.publish_state(object_id=object_id, value=float(u["value"]))

                                    # After UseCase exchange, subscribe + read measurement once (best-effort).
                                    if (
                                        peer_use_case_received
                                        and remote_device_address is not None
                                        and selected_measurement_servers
                                        and not measurement_subscription_sent
                                    ):
                                        measurement_subscription_sent = True
                                        for server in selected_measurement_servers:
                                            await subscribe_remote_measurement(
                                                ws,
                                                local_device_address=local_device_address,
                                                remote_device_address=remote_device_address,
                                                remote_measurement_feature=server,
                                                msg_counter=msg_counter,
                                            )
                                    if (
                                        peer_use_case_received
                                        and remote_device_address is not None
                                        and selected_measurement_servers
                                        and not measurement_read_sent
                                    ):
                                        measurement_read_sent = True
                                        for server in selected_measurement_servers:
                                            await request_remote_measurement_once(
                                                ws,
                                                local_device_address=local_device_address,
                                                remote_device_address=remote_device_address,
                                                remote_measurement_feature=server,
                                                msg_counter=msg_counter,
                                            )

                                    # Keep output short for large payloads
                                    print(f"   Recv cmd keys: {list(cmd.keys())}")
                                except Exception as e:
                                    print(f"⚠️  [SPINE] Fehler beim Verarbeiten: {e}")

                            except Exception as e:
                                print(f"\n📨 Nachricht #{message_count} (SPINE decode error): {e}")
                                print(f"   Raw: {data[:200].hex()}...")
                        else:
                            print(f"\n📨 Nachricht #{message_count} (Binär): {data.hex()}")
                    else:
                        print(f"\n📨 Nachricht #{message_count}: {data}")
                        
            except Exception as e:
                code = getattr(e, "code", None)
                reason = getattr(e, "reason", None)
                print(f"\n❌ WebSocket beendet: code={code} reason={reason} err={e}")
                if last_control_msg is not None:
                    print("\n🧾 Letzte SHIP-Control Nachricht vor Close:")
                    print(json.dumps(last_control_msg, indent=2)[:5000])
                if last_spine_header is not None and last_spine_cmd is not None:
                    print("\n🧾 Letzte SPINE Nachricht vor Close:")
                    print(json.dumps({"header": last_spine_header, "cmd": last_spine_cmd}, indent=2)[:5000])

    except Exception as e:
        print(f"❌ Fehler: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            mqtt_pub.close()
        except Exception:
            pass
        await aiozc.async_unregister_all_services()
        await aiozc.async_close()
        print("👋 Beendet.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Abgebrochen durch Benutzer")