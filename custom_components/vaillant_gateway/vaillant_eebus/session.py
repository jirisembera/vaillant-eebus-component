"""One connected SPINE session.

A :class:`_SpineSession` wraps a single live SHIP WebSocket and drives the
SPINE protocol over it: a continuous background receive loop dispatches every
inbound frame to the per-feature handlers and latches the gateway-level signals
(remote device address, detailed discovery), while a single linear
:meth:`setup` run issues the bring-up request frames in order and awaits the
replies. :meth:`read_values` re-reads the current values afterwards.

This is the protocol-engine half of :mod:`vaillant_eebus.client`; the public,
consumer-facing :class:`vaillant_eebus.client.EebusClient` owns one of these
per connection and exposes the high-level API on top of it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from .eebus_json import json_from_eebus_json
from .errors import EebusError, EebusWriteError
from .handlers import FeatureHandler
from .parsers import (
    entity_addr_list,
    extract_entities,
    extract_heat_pump_entity,
)
from .ship import send_ship_json
from .spine import (
    SpineAddress,
    SpineChannel,
    WriteFrame,
    parse_spine_datagram,
    send_spine_result_ok,
    send_spine_write,
)
from .spine_replies import handle_spine_read
from .spine_requests import request_remote_detailed_discovery
from .utils import MsgCounter

logger = logging.getLogger(__name__)
trace = logging.getLogger(__name__ + ".trace")

# Bring-up tuning. Discovery is fatal (no topology without it); descriptions and
# values degrade gracefully (a missing feature is logged and simply absent).
_DISCOVERY_TIMEOUT = 10.0
_DESC_ATTEMPTS = 3
_DESC_TIMEOUT = 5.0
_VALUES_TIMEOUT = 10.0


def _log_discovery_summary(discovery: Dict[str, Any]) -> None:
    hp_entity = extract_heat_pump_entity(discovery)
    hp_prefix = entity_addr_list(hp_entity) if isinstance(hp_entity, dict) else None
    if hp_prefix is not None:
        logger.debug("✅ [DISCOVERY] entityType=HeatPumpAppliance at entity=%s", hp_prefix)
    for e in extract_entities(discovery):
        ent = e.get("entity")
        if isinstance(ent, list) and all(isinstance(x, int) for x in ent):
            logger.debug(
                "  entity=%s type=%s desc=%s",
                ent,
                e.get("entityType"),
                e.get("description"),
            )


class _SpineSession:
    """One connected SPINE session.

    A single background receive loop dispatches every inbound frame to the
    per-feature handlers and latches the gateway-level signals (remote device
    address, discovery). Bring-up is a single linear :meth:`setup` run — remote
    address → discovery → descriptions → (values) → (subscribe) — after which the
    session is simply usable. The value-read and subscribe steps are opt-in;
    :meth:`read_values` and :meth:`subscribe` run them later on demand.
    """

    def __init__(
        self,
        *,
        ws,
        local_device_address: str,
        handlers: List[FeatureHandler],
    ) -> None:
        self._ws = ws
        self._local_device_address = local_device_address
        self._msg_counter = MsgCounter(start=1)
        self._handlers = handlers
        self._cmd_dispatch: Dict[str, FeatureHandler] = {
            k: h for h in handlers for k in h.handled_cmd_keys
        }
        self._remote_device_address: Optional[str] = None
        self._remote_address_event = asyncio.Event()
        self._discovery: Optional[Dict[str, Any]] = None
        self._discovery_event = asyncio.Event()
        # Built once the remote device address is latched (in setup()); bundles
        # ws + both device addresses + the counter for the request helpers.
        self._channel: Optional[SpineChannel] = None

        # Outstanding write requests, keyed by the msgCounter of the
        # outbound `write` frame; the future is resolved (or rejected) when
        # the gateway's matching `result` frame arrives.
        self._pending_writes: Dict[int, asyncio.Future] = {}

        self._message_count = 0
        self._last_control_msg: Optional[Dict[str, Any]] = None
        self._last_spine_header: Optional[Dict[str, Any]] = None
        self._last_spine_cmd: Optional[Dict[str, Any]] = None

    # ── public accessors ────────────────────────────────────────────────

    @property
    def remote_device_address(self) -> Optional[str]:
        return self._remote_device_address

    @property
    def discovery(self) -> Optional[Dict[str, Any]]:
        return self._discovery

    # ── receive loop ────────────────────────────────────────────────────

    async def run_receive_loop(self) -> None:
        """Drain frames forever, dispatching each one to handlers.

        Terminates only when the websocket is closed or the task is
        cancelled. Exceptions from the recv path are surfaced to the
        caller (the EebusClient._run wrapper).
        """
        try:
            while True:
                hdr, cmd = await self._recv_next_spine()
                self._latch_remote_address(hdr)
                self._latch_node_management_replies(hdr, cmd)
                await self._handle_frame(hdr, cmd)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._log_close(e)
            raise

    def _latch_remote_address(self, hdr: Dict[str, Any]) -> None:
        if self._remote_device_address is not None:
            return
        addr = SpineAddress.from_raw(hdr.get("addressSource"))
        if addr is not None and addr.device:
            self._remote_device_address = addr.device
            self._remote_address_event.set()

    def _latch_node_management_replies(self, hdr: Dict[str, Any], cmd: Dict[str, Any]) -> None:
        if hdr.get("cmdClassifier") != "reply":
            return
        if "nodeManagementDetailedDiscoveryData" in cmd and not self._discovery_event.is_set():
            discovery = cmd.get("nodeManagementDetailedDiscoveryData")
            if isinstance(discovery, dict):
                self._discovery = discovery
                _log_discovery_summary(discovery)
                for h in self._handlers:
                    h.set_servers_from_discovery(discovery)
                self._discovery_event.set()

    # ── linear bring-up ─────────────────────────────────────────────────

    async def setup(self, *, subscribe: bool = False, read_values: bool = False) -> None:
        """Bring the session up, in order, exactly once.

        remote address → discovery → descriptions → (values) → (subscribe).
        The value-read and subscribe steps are opt-in (``read_values`` /
        ``subscribe``); the caller can also run them later via :meth:`read_values`
        / :meth:`subscribe`.

        Discovery failure is fatal (raises :class:`EebusError` — there is no
        topology without it). A per-feature description/value gap is *not*
        fatal: it is logged at ERROR and that feature is simply absent from the
        topology / values, so callers drop the corresponding entity.
        """
        await self._remote_address_event.wait()
        assert self._remote_device_address is not None
        self._channel = SpineChannel(
            ws=self._ws,
            local_device_address=self._local_device_address,
            remote_device_address=self._remote_device_address,
            msg_counter=self._msg_counter,
        )
        await self._run_discovery()
        await self._run_descriptions()
        if read_values:
            await self._run_read_values()
        if subscribe:
            await self._run_subscribe()

    async def _run_discovery(self) -> None:
        """Send detailed-discovery and wait for the reply (fatal on timeout)."""
        if self._discovery_event.is_set():
            return
        assert self._channel is not None
        await request_remote_detailed_discovery(self._channel)
        try:
            await asyncio.wait_for(self._discovery_event.wait(), timeout=_DISCOVERY_TIMEOUT)
        except asyncio.TimeoutError as err:
            raise EebusError(
                f"Detailed discovery timed out after {_DISCOVERY_TIMEOUT:.0f}s"
            ) from err

    async def _run_descriptions(self) -> None:
        """Read every handler's descriptions, retrying laggards a few times.

        Each handler's ``request_descriptions`` re-sends only while its own
        ``descriptions_ready`` is unset, so retries target just the laggards.
        Handlers still pending after the last attempt are logged at ERROR and
        left absent — :attr:`descriptions_ready` stays honest (unset).
        """
        assert self._channel is not None
        for _attempt in range(_DESC_ATTEMPTS):
            if all(h.descriptions_ready.is_set() for h in self._handlers):
                return
            for h in self._handlers:
                await h.request_descriptions(self._channel)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(h.descriptions_ready.wait() for h in self._handlers)),
                    timeout=_DESC_TIMEOUT,
                )
                return
            except asyncio.TimeoutError:
                continue
        for h in self._handlers:
            if not h.descriptions_ready.is_set():
                logger.error(
                    "❌ No descriptions for %s feature after %d attempts "
                    "(servers=%s) — its entities will be unavailable",
                    h.feature_type,
                    _DESC_ATTEMPTS,
                    h.servers,
                )

    async def _run_read_values(self, *, timeout: float = _VALUES_TIMEOUT) -> None:
        """Issue a one-shot read of every handler's current values."""
        assert self._channel is not None
        for h in self._handlers:
            await h.request_values(self._channel)
        try:
            await asyncio.wait_for(
                asyncio.gather(*(h.values_ready.wait() for h in self._handlers)),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            for h in self._handlers:
                if h.value_cmd_keys and not h.values_ready.is_set():
                    logger.error(
                        "❌ No values for %s feature within %.0fs — "
                        "its entities will be unavailable",
                        h.feature_type,
                        timeout,
                    )

    async def _run_subscribe(self) -> None:
        """Subscribe each server to value notifications (no synchronous reply)."""
        assert self._channel is not None
        for h in self._handlers:
            await h.subscribe(self._channel)

    async def read_values(self) -> None:
        """Re-read every handler's current values (for write read-back / re-poll).

        Assumes :meth:`setup` has already run.
        """
        await self._run_read_values()

    async def subscribe(self) -> None:
        """Subscribe each server to value notifications (opt-in after setup).

        Assumes :meth:`setup` has already run.
        """
        await self._run_subscribe()

    # ── per-frame work ──────────────────────────────────────────────────

    async def _handle_frame(self, hdr: Dict[str, Any], cmd: Dict[str, Any]) -> None:
        """ACK, answer any `read`, dispatch `notify`/`reply` to handlers."""
        cmd_classifier = hdr.get("cmdClassifier")
        try:
            if cmd_classifier != "result":
                await send_spine_result_ok(
                    self._ws,
                    request_header=hdr,
                    local_device_address=self._local_device_address,
                    msg_counter=self._msg_counter,
                )
            if cmd_classifier == "read":
                await handle_spine_read(
                    self._ws,
                    request_header=hdr,
                    cmd=cmd,
                    local_device_address=self._local_device_address,
                    msg_counter=self._msg_counter,
                )
            elif cmd_classifier in ("reply", "notify"):
                for cmd_key in cmd:
                    handler = self._cmd_dispatch.get(cmd_key)
                    if handler is not None:
                        handler.handle(cmd_key, hdr, cmd)
                        break
            elif cmd_classifier == "result":
                self._resolve_pending_write(hdr, cmd)
        except Exception as e:
            logger.warning("[SPINE] Error while processing: %s", e)

    async def write(self, frame: WriteFrame, *, timeout: float) -> None:
        """Send a SPINE ``write`` and await the gateway's ``result``.

        The single owner of write transport: it allocates the ``msgCounter``,
        **registers the correlation future before sending**, then sends ``frame``
        on this session's websocket. The receive loop resolves/rejects that
        future when the matching ``result`` arrives (see
        :meth:`_resolve_pending_write`). Registering first is deliberate — the
        gateway's ``result`` references this counter, so the future must already
        exist when the reply is processed; that way correctness never rests on
        the absence of a suspension point between the send and the registration.

        Handlers build the :class:`WriteFrame` with a device-less destination;
        the session supplies the connected device (which it owns) here. Handlers
        never touch the websocket or the counter. Raises
        :class:`EebusWriteError` on a non-zero ``errorNumber``, on timeout, or if
        the session is torn down before a reply arrives.
        """
        assert self._remote_device_address is not None
        mc = await self._msg_counter.next()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_writes[mc] = fut
        try:
            await send_spine_write(
                self._ws,
                frame,
                remote_device_address=self._remote_device_address,
                msg_counter=mc,
            )
            await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise EebusWriteError(
                f"SPINE write timed out after {timeout:.1f}s (msgCounter={mc})"
            ) from None
        finally:
            self._pending_writes.pop(mc, None)

    def _resolve_pending_write(self, hdr: Dict[str, Any], cmd: Dict[str, Any]) -> None:
        """Match an inbound ``result`` frame to a waiting write future."""
        ref = hdr.get("msgCounterReference")
        if not isinstance(ref, int):
            return
        fut = self._pending_writes.pop(ref, None)
        if fut is None or fut.done():
            return
        result_data = cmd.get("resultData") if isinstance(cmd, dict) else None
        if not isinstance(result_data, dict):
            result_data = {}
        err = result_data.get("errorNumber", 0)
        if not isinstance(err, int):
            err = 0
        if err == 0:
            fut.set_result(None)
            return
        desc = result_data.get("description")
        fut.set_exception(
            EebusWriteError(
                f"SPINE write failed (errorNumber={err}, description={desc!r})",
                error_number=err,
                description=desc if isinstance(desc, str) else None,
            )
        )

    def fail_pending_writes(self, exc: BaseException) -> None:
        """Reject every outstanding write future. Used on session teardown."""
        pending = self._pending_writes
        self._pending_writes = {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(exc)

    # ── receive / decode ────────────────────────────────────────────────

    async def _recv_next_spine(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        while True:
            frame = await self._recv_one()
            if frame is not None:
                self._last_spine_header, self._last_spine_cmd = frame
                return frame

    async def _recv_one(self) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """One recv iteration. Returns a parsed SPINE frame, or None for
        control/keep-alive/undecodable frames (caller should loop).
        """
        try:
            data = await asyncio.wait_for(self._ws.recv(), timeout=60)
        except asyncio.TimeoutError:
            await self._send_keepalive()
            return None
        self._message_count += 1

        if isinstance(data, (bytes, bytearray)):
            trace.debug("> %s", bytes(data[1:]))

        if not (isinstance(data, bytes) and len(data) > 0):
            logger.debug("📨 Message #%d: %s", self._message_count, data)
            return None

        if data[0] == 1:  # SHIP Control
            try:
                payload_text = data[1:].decode("utf-8", errors="ignore")
                payload_text = json_from_eebus_json(payload_text)
                msg = json.loads(payload_text)
                if isinstance(msg, dict):
                    self._last_control_msg = msg
                logger.debug("📨 Message #%d:", self._message_count)
                logger.debug("%s", json.dumps(msg, indent=2))
            except (ValueError, TypeError):
                logger.debug("📨 Message #%d (binary): %s", self._message_count, data.hex())
            return None

        if data[0] != 2:  # Not SHIP Data either
            logger.debug("📨 Message #%d (binary): %s", self._message_count, data.hex())
            return None

        # SHIP Data (SPINE)
        try:
            payload_text = data[1:].decode("utf-8", errors="ignore")
            payload_text = json_from_eebus_json(payload_text)
            msg = json.loads(payload_text)
        except Exception as e:
            logger.warning("Message #%d (SPINE decode error): %s", self._message_count, e)
            logger.debug("Raw: %s...", data[:200].hex())
            return None

        parsed = parse_spine_datagram(msg)
        if parsed is None:
            logger.debug("📨 SPINE #%d (unparsed):", self._message_count)
            logger.debug("%s", json.dumps(msg, indent=2)[:5000])
            return None

        return parsed

    async def _send_keepalive(self) -> None:
        try:
            await send_ship_json(
                self._ws, {"connectionHello": {"phase": "ready", "waiting": 60000}}
            )
            logger.debug("📤 [HELLO] Keep-alive sent (ready)")
        except Exception as e:
            logger.warning("[HELLO] Keep-alive failed: %s", e)

    def _log_close(self, e: BaseException) -> None:
        code = getattr(e, "code", None)
        reason = getattr(e, "reason", None)
        logger.error("WebSocket closed: code=%s reason=%s err=%s", code, reason, e)
        if self._last_control_msg is not None:
            logger.debug("🧾 Last SHIP control message before close:")
            logger.debug("%s", json.dumps(self._last_control_msg, indent=2)[:5000])
        if self._last_spine_header is not None and self._last_spine_cmd is not None:
            logger.debug("🧾 Last SPINE message before close:")
            logger.debug(
                "%s",
                json.dumps(
                    {"header": self._last_spine_header, "cmd": self._last_spine_cmd}, indent=2
                )[:5000],
            )
