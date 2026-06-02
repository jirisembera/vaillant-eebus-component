"""SPINE framing helpers.

SPINE datagrams travel inside SHIP Data frames. These helpers build SPINE
read/call/result envelopes and parse incoming SPINE messages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Dict, Optional, Tuple

from .ship import send_ship_data
from .utils import MsgCounter

logger = logging.getLogger(__name__)

# The device-less map key shared by handlers and topology: an entity address
# path plus a feature id. Defined here (the lowest address-handling layer) and
# re-exported from ``handlers.base`` so there is a single definition.
FeatureKey = Tuple[Tuple[int, ...], int]


@dataclass(frozen=True)
class SpineAddress:
    """A SPINE feature address: an entity path, an optional feature, an optional device.

    ``device`` is None for device-agnostic addresses — a discovered server
    feature, the source stamped on a parsed update, a handler's own local client
    feature before the session binds the connected gateway. ``feature`` is None
    for entity-only addresses (e.g. an entity address from discovery).

    :meth:`from_raw` is the single validated parse from an on-wire address dict
    and :meth:`to_raw` serializes back; everything in between works with the
    typed fields, so the ``isinstance(entity, list) and all(int)`` idiom lives
    in exactly one place.
    """

    entity: Tuple[int, ...] = ()
    feature: Optional[int] = None
    device: Optional[str] = None

    @classmethod
    def from_raw(cls, addr: Any) -> Optional["SpineAddress"]:
        """Parse a SPINE address dict; None if the entity is missing/malformed."""
        if not isinstance(addr, dict):
            return None
        ent = addr.get("entity")
        if not (isinstance(ent, list) and ent and all(isinstance(x, int) for x in ent)):
            return None
        feat = addr.get("feature")
        dev = addr.get("device")
        return cls(
            entity=tuple(int(x) for x in ent),
            feature=int(feat) if isinstance(feat, int) else None,
            device=dev if isinstance(dev, str) and dev else None,
        )

    @property
    def feature_key(self) -> Optional[FeatureKey]:
        """The device-less ``(entity, feature)`` map key, or None without a feature."""
        if self.feature is None:
            return None
        return (self.entity, self.feature)

    @property
    def feature_or_zero(self) -> int:
        """The feature id, or ``0`` for a feature-less address.

        Update sources stamp ``source_feature`` from a parsed address that may
        carry no feature; ``0`` is the agreed fallback (see the key formulas in
        :mod:`vaillant_eebus.keys`).
        """
        return self.feature if self.feature is not None else 0

    def with_device(self, device: str) -> "SpineAddress":
        """A copy bound to ``device`` (the session injects the connected gateway)."""
        return replace(self, device=device)

    def to_raw(self) -> Dict[str, Any]:
        """Serialize to the on-wire ``{device?, entity, feature?}`` dict."""
        out: Dict[str, Any] = {}
        if self.device is not None:
            out["device"] = self.device
        out["entity"] = list(self.entity)
        if self.feature is not None:
            out["feature"] = self.feature
        return out


@dataclass(frozen=True)
class WriteFrame:
    """A validated SPINE write, ready for :meth:`_SpineSession.write`.

    Built by a handler's ``build_write`` (which owns the validation + address /
    cmd construction) and consumed by the session (which owns the transport).
    ``address_destination`` is a device-less :class:`SpineAddress` (target entity
    + feature only): the remote device is connection-scoped, so the session binds
    it at send time. ``address_source`` is the handler's own, statically known
    local feature address.
    """

    address_source: SpineAddress
    address_destination: SpineAddress
    cmd: Dict[str, Any]


@dataclass(frozen=True)
class SpineChannel:
    """Session-scoped context for sending an addressed SPINE frame.

    Bundles the four connection constants every request helper needs — the
    websocket, our own and the peer's SPINE device addresses, and the shared
    :class:`MsgCounter`. The session builds one once it has latched the remote
    device address and hands it to the handlers, so their request methods take a
    single channel instead of re-threading ``ws`` / addresses / counter on every
    call. It is the SPINE-layer descendant of :class:`connection.Connected`:
    same websocket + local address, now also carrying the remote SPINE address
    (unknown at connect time) and the counter (owned by the SPINE session).
    """

    ws: Any
    local_device_address: str
    remote_device_address: str
    msg_counter: MsgCounter


def spine_addr(*, device: str, entity: int, feature: int) -> SpineAddress:
    """Convenience builder for a single-entity SPINE feature address."""
    return SpineAddress(entity=(entity,), feature=feature, device=device)


# Local node feature layout. Our SHIP node presents a DeviceInformation entity
# ([0], carrying NodeManagement + a DeviceClassification server) and a CEM entity
# ([1]) whose client features mirror the gateway's server features. These ids are
# advertised in our detailed-discovery reply
# (:func:`vaillant_eebus.spine_replies.build_local_detailed_discovery`) *and* used to
# address our own client features
# (:meth:`vaillant_eebus.client.EebusClient._build_handlers`); both sides must agree,
# so they live here once.
LOCAL_DEVICE_ENTITY = 0
LOCAL_CEM_ENTITY = 1

LOCAL_FEATURE_NODE_MANAGEMENT = 0  # on LOCAL_DEVICE_ENTITY
LOCAL_FEATURE_DEVICE_CLASSIFICATION = 1  # on LOCAL_DEVICE_ENTITY (server)
LOCAL_FEATURE_MEASUREMENT = 1  # on LOCAL_CEM_ENTITY (client)
LOCAL_FEATURE_SENSING = 2
LOCAL_FEATURE_HVAC = 3
LOCAL_FEATURE_SETPOINT = 4
LOCAL_FEATURE_ELECTRICAL = 5


def first_cmd(payload_cmd: Any) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of the first SPINE cmd object.

    Some peers wrap cmd as [[{...}]] instead of [{...}] — handle both.
    """
    if isinstance(payload_cmd, dict):
        return payload_cmd

    if not isinstance(payload_cmd, list) or not payload_cmd:
        return None

    first = payload_cmd[0]
    if isinstance(first, list) and first:
        inner = first[0]
        return inner if isinstance(inner, dict) else None
    return first if isinstance(first, dict) else None


def parse_spine_datagram(
    message: Dict[str, Any],
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
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

    cmd = first_cmd(d_payload.get("cmd"))
    if cmd is None:
        return None

    return header, cmd


def make_reply_addresses(
    request_header: Dict[str, Any],
    *,
    local_device_address: str,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return (address_source, address_destination) for replies/results.

    SPINE replies swap source/destination. Some devices omit the device field
    in addressDestination in requests; we always set our device on addressSource
    when the peer included a device field there.
    """
    address_destination = request_header.get("addressSource")
    address_source = request_header.get("addressDestination")
    if not isinstance(address_destination, dict) or not isinstance(address_source, dict):
        return None

    address_source = dict(address_source)
    # Important interop detail:
    # Some devices omit the "device" field in addressDestination when addressing the local side.
    # Mirror the structure the peer used (do not force-inject "device" unless it was present),
    # otherwise some peers treat it as a protocol error.
    if "device" in address_source:
        address_source["device"] = local_device_address

    return address_source, dict(address_destination)


async def _send_spine_datagram(
    ws,
    *,
    cmd_classifier: str,
    address_source: Dict[str, Any],
    address_destination: Dict[str, Any],
    cmd: Dict[str, Any],
    msg_counter: int,
    specification_version: str = "1.3.0",
    msg_counter_reference: Optional[int] = None,
    ack_request: Optional[bool] = None,
) -> None:
    """Build and send one SPINE datagram — the shared core of every sender.

    Header fields are inserted in the order the SPINE peers expect: requests
    (read/call/write) carry ``cmdClassifier`` then ``ackRequest``; responses
    (result) carry ``msgCounterReference`` before ``cmdClassifier`` and no
    ``ackRequest``. Each optional field is added only when supplied, so the
    on-wire field order matches what each classifier used before this was
    factored out.
    """
    header: Dict[str, Any] = {
        "specificationVersion": specification_version,
        "addressSource": address_source,
        "addressDestination": address_destination,
        "msgCounter": msg_counter,
    }
    if msg_counter_reference is not None:
        header["msgCounterReference"] = msg_counter_reference
    header["cmdClassifier"] = cmd_classifier
    if ack_request is not None:
        header["ackRequest"] = ack_request
    await send_ship_data(ws, {"datagram": {"header": header, "payload": {"cmd": [cmd]}}})


async def send_spine_read(
    ws,
    *,
    address_source: SpineAddress,
    address_destination: SpineAddress,
    cmd: Dict[str, Any],
    msg_counter: MsgCounter,
    specification_version: str = "1.3.0",
    ack_request: bool = True,
) -> None:
    """Send a SPINE read datagram to the remote device."""
    logger.debug("📤 [SPINE] Read send: %s", list(cmd.keys()))
    await _send_spine_datagram(
        ws,
        cmd_classifier="read",
        address_source=address_source.to_raw(),
        address_destination=address_destination.to_raw(),
        cmd=cmd,
        msg_counter=await msg_counter.next(),
        specification_version=specification_version,
        ack_request=ack_request,
    )


async def send_spine_call(
    ws,
    *,
    address_source: SpineAddress,
    address_destination: SpineAddress,
    cmd: Dict[str, Any],
    msg_counter: MsgCounter,
    specification_version: str = "1.3.0",
    ack_request: bool = True,
) -> None:
    """Send a SPINE call datagram to the remote device."""
    logger.debug("📤 [SPINE] Call send: %s", list(cmd.keys()))
    await _send_spine_datagram(
        ws,
        cmd_classifier="call",
        address_source=address_source.to_raw(),
        address_destination=address_destination.to_raw(),
        cmd=cmd,
        msg_counter=await msg_counter.next(),
        specification_version=specification_version,
        ack_request=ack_request,
    )


async def send_spine_write(
    ws,
    frame: WriteFrame,
    *,
    remote_device_address: str,
    msg_counter: int,
    specification_version: str = "1.3.0",
    ack_request: bool = True,
) -> None:
    """Send a SPINE write datagram to ``remote_device_address``.

    The session supplies both connection-scoped values — the remote device
    (bound onto the frame's device-less destination) and a caller-allocated
    ``msgCounter`` (via :meth:`MsgCounter.next`) — so it can register its
    result-correlation future *before* the frame goes out (the gateway's
    ``result`` references this counter via ``msgCounterReference``). See
    :meth:`vaillant_eebus.session._SpineSession.write`.
    """
    address_destination = frame.address_destination.with_device(remote_device_address)
    logger.debug("📤 [SPINE] Write send: %s (msgCounter=%s)", list(frame.cmd.keys()), msg_counter)
    await _send_spine_datagram(
        ws,
        cmd_classifier="write",
        address_source=frame.address_source.to_raw(),
        address_destination=address_destination.to_raw(),
        cmd=frame.cmd,
        msg_counter=msg_counter,
        specification_version=specification_version,
        ack_request=ack_request,
    )


async def send_spine_result_ok(
    ws,
    *,
    request_header: Dict[str, Any],
    local_device_address: str,
    msg_counter: MsgCounter,
) -> None:
    """Send SPINE cmdClassifier='result' (errorNumber 0) acknowledging a received datagram."""
    ref = request_header.get("msgCounter")
    if ref is None:
        return

    addresses = make_reply_addresses(request_header, local_device_address=local_device_address)
    if addresses is None:
        return

    address_source, address_destination = addresses
    await _send_spine_datagram(
        ws,
        cmd_classifier="result",
        address_source=address_source,
        address_destination=address_destination,
        cmd={"resultData": {"errorNumber": 0}},
        msg_counter=await msg_counter.next(),
        specification_version=request_header.get("specificationVersion", "1.3.0"),
        msg_counter_reference=ref,
    )
    logger.debug("📤 [SPINE] Result send: msgCounterReference=%s", ref)
