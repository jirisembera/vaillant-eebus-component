"""Shared connection establishment: mDNS discovery + TLS + WebSocket + SHIP handshake.

Used by both :class:`vaillant_eebus.client.EebusClient` (which then drives the SPINE
loop) and :class:`vaillant_eebus.pairing.EebusPairing` (which connects only to complete
the trust handshake).
"""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional, cast

from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from .cert import get_or_create_certificate
from .errors import GatewayNotFoundError, HandshakeFailedError
from .mdns import MDNSHandler, _txt, local_ipv4_addresses
from .ship import perform_ship_handshake

logger = logging.getLogger(__name__)


@dataclass
class Gateway:
    """Information about a discovered EEBUS gateway.

    ``brand`` / ``model`` / ``name`` come straight from the gateway's mDNS TXT
    record (``brand``, ``model`` and the device ``id`` respectively) so callers
    don't have to hardcode "Vaillant" / "VR921".
    """

    ip: str
    port: int
    ski: str
    brand: str = ""
    model: str = ""
    name: str = ""


@dataclass
class LocalNode:
    """Our own SHIP presence on the LAN, set up once by :func:`open_node`.

    Bundles the zeroconf instance (with our ``_ship._tcp.local.`` service already
    announced), our identity derived from the client cert, and the TLS context —
    everything :func:`open_connection` needs to dial a gateway. The caller holds
    one of these for a whole session; gateways are discovered on ``aiozc`` and
    connected to with the node, so no our-side plumbing is threaded any further.
    """

    aiozc: AsyncZeroconf
    ski: str
    local_ship_id: str
    local_device_address: str
    ssl_ctx: "ssl.SSLContext"


@dataclass
class Connected:
    """An established SHIP-layer session, ready for SPINE traffic."""

    ws: Any
    local_device_address: str
    gateway: Gateway


def _build_ssl_context(cert_file: str, key_file: str) -> ssl.SSLContext:
    """Build the TLS context used to talk to the gateway.

    Intentionally permissive: Vaillant negotiates legacy cipher suites and
    we don't validate the gateway certificate (trust is established via
    SKI exchange at the SHIP layer).

    Positional args — kept that way so it can be dispatched through
    ``loop.run_in_executor`` (which doesn't take kwargs).
    """
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers("HIGH:!aNULL:!eNULL:!MD5@SECLEVEL=1")
    return ctx


@asynccontextmanager
async def private_aiozc(
    bind_address: Optional[str] = None,
) -> AsyncIterator[AsyncZeroconf]:
    """Yield a private IPv4 ``AsyncZeroconf`` pinned to ``bind_address`` if given,
    and close it on exit.

    The throwaway-zeroconf scope for callers that don't open a full
    :func:`open_node` — read-only discovery (``tools/discover.py``) wraps its
    :func:`discover_gateways` call in this. With no bind address it browses every
    interface (zeroconf's ``InterfaceChoice.All`` default). :func:`open_node`
    reuses it for its own private-instance branch, so the create/close lifecycle
    lives in exactly one place.
    """
    if bind_address:
        aiozc = AsyncZeroconf(ip_version=IPVersion.V4Only, interfaces=[bind_address])
    else:
        aiozc = AsyncZeroconf(ip_version=IPVersion.V4Only)
    try:
        yield aiozc
    finally:
        try:
            await aiozc.async_close()
        except Exception:
            pass


async def _safe_unregister(aiozc: AsyncZeroconf, info: AsyncServiceInfo) -> None:
    """Best-effort service unregister on teardown (swallows late-shutdown errors)."""
    try:
        await aiozc.async_unregister_service(info)
    except Exception:
        pass


@asynccontextmanager
async def open_node(
    *,
    cert_file: str = "cert.pem",
    key_file: str = "key.pem",
    bind_address: Optional[str] = None,
    mdns_port: int = 54885,
    service_name_prefix: str = "Python",
    aiozc: Optional[AsyncZeroconf] = None,
) -> AsyncIterator[LocalNode]:
    """Set up our local SHIP presence and yield a :class:`LocalNode`.

    The single setup step for a connecting/pairing session: create (or reuse)
    the zeroconf instance, load the client cert (our SKI), build the TLS context,
    and register our own ``_ship._tcp.local.`` announcement so the myVAILLANT app
    can show us in the trust dialog. Discover gateways on ``node.aiozc`` and dial
    them with :func:`open_connection` / the high-level classes — none of them
    need our-side plumbing again.

    When ``aiozc`` is supplied (HA's shared instance) it is used as-is and only
    our own service is unregistered on exit; otherwise a private instance is
    created (pinned to ``bind_address`` or all-interface) and closed on exit.
    """
    loop = asyncio.get_running_loop()

    ski = await loop.run_in_executor(None, get_or_create_certificate, cert_file, key_file)
    ssl_ctx = await loop.run_in_executor(None, _build_ssl_context, cert_file, key_file)
    local_ship_id = f"{service_name_prefix.lower()}-{ski[:12]}"
    local_device_address = f"d:_i:1_{local_ship_id}"

    advertised = [bind_address] if bind_address else local_ipv4_addresses()
    desc = {"txtvers": "1", "path": "/ship/", "ski": ski, "register": "true"}
    registered_info = AsyncServiceInfo(
        "_ship._tcp.local.",
        f"{service_name_prefix}-{ski[:6]}._ship._tcp.local.",
        addresses=[socket.inet_aton(a) for a in advertised],
        port=mdns_port,
        properties=desc,
    )
    async with AsyncExitStack() as stack:
        # Own a private instance only when the caller didn't supply one (HA passes
        # its shared aiozc); the CM closes it on exit. The service announcement is
        # registered — and unregistered on teardown — either way.
        if aiozc is None:
            aiozc = await stack.enter_async_context(private_aiozc(bind_address))

        await aiozc.async_register_service(registered_info)
        logger.debug("📢 mDNS announced: %s", ", ".join(advertised) or "all interfaces")
        stack.push_async_callback(_safe_unregister, aiozc, registered_info)
        yield LocalNode(
            aiozc=aiozc,
            ski=ski,
            local_ship_id=local_ship_id,
            local_device_address=local_device_address,
            ssl_ctx=ssl_ctx,
        )


def _to_gateway(info: AsyncServiceInfo) -> Gateway:
    return Gateway(
        ip=socket.inet_ntoa(info.addresses[0]) if info.addresses else "",
        port=info.port or 0,
        ski=_txt(info.properties, b"ski"),
        brand=_txt(info.properties, b"brand"),
        model=_txt(info.properties, b"model"),
        name=_txt(info.properties, b"id") or info.name,
    )


def select_gateway(gateways: list, target_ski: Optional[str]) -> Optional[Gateway]:
    """Pick a gateway from a discovered list: the one with ``target_ski`` if
    given, else the first found (``None`` if the list is empty / no match)."""
    if target_ski:
        return next((g for g in gateways if g.ski == target_ski), None)
    return gateways[0] if gateways else None


async def discover_gateways(
    *,
    aiozc: AsyncZeroconf,
    timeout: int = 5,
    wait_for_ski: Optional[str] = None,
    first_only: bool = False,
) -> list:
    """Browse mDNS and return every Vaillant EEBUS gateway found.

    Read-only: registers nothing, connects to nothing. Browses on the supplied
    ``aiozc`` (its interface selection is already baked in — typically
    ``node.aiozc`` from :func:`open_node`, or HA's shared instance). Returns a list of
    :class:`Gateway`. Waits up to ``timeout`` seconds; with ``wait_for_ski`` or
    ``first_only`` it returns as soon as the wanted (or any) gateway appears, so
    the connect path stays fast, otherwise it waits the whole window to
    enumerate every gateway (e.g. ``python3 tools/discover.py``).
    """
    handler = MDNSHandler()
    browser = AsyncServiceBrowser(aiozc.zeroconf, "_ship._tcp.local.", handler)
    logger.debug("🔎 Searching for EEBUS gateways (mDNS _ship._tcp.local.)...")
    try:
        waited = 0.0
        while waited < timeout:
            await asyncio.sleep(0.5)
            waited += 0.5
            if wait_for_ski and wait_for_ski in handler.gateways:
                break
            if first_only and handler.gateways:
                break
    finally:
        await browser.async_cancel()
    return [_to_gateway(info) for info in handler.gateways.values()]


async def discover_gateway(
    *,
    aiozc: AsyncZeroconf,
    mdns_timeout: int = 30,
    target_ski: Optional[str] = None,
) -> Gateway:
    """Discover and return a single gateway, raising if none matches.

    Convenience over :func:`discover_gateways` + :func:`select_gateway`: returns
    early on ``target_ski`` (or the first gateway when ``None``). Raises
    :class:`GatewayNotFoundError` when no (matching) gateway answers in time.
    """
    gateways = await discover_gateways(
        aiozc=aiozc,
        timeout=mdns_timeout,
        wait_for_ski=target_ski,
        first_only=target_ski is None,
    )
    gateway = select_gateway(gateways, target_ski)
    if gateway is None:
        suffix = f" with SKI {target_ski}" if target_ski else ""
        raise GatewayNotFoundError(
            f"No EEBUS gateway found{suffix} (mDNS timeout after {mdns_timeout}s)."
        )
    logger.debug("✅ Gateway selected: %s:%d (SKI=%s)", gateway.ip, gateway.port, gateway.ski)
    return gateway


@asynccontextmanager
async def open_connection(gateway: Gateway, *, node: LocalNode) -> AsyncIterator[Connected]:
    """Open a SHIP session to an already-discovered :class:`Gateway`.

    Does no discovery and no setup — pass a gateway from :func:`discover_gateway`
    and the :class:`LocalNode` from :func:`open_node` (which already announced us
    and built the TLS context). Opens the TLS WebSocket to ``gateway`` and
    performs the SHIP handshake, yielding a ready :class:`Connected`.

    Raises:
        HandshakeFailedError: the SHIP handshake did not complete.
    """
    import websockets

    logger.debug("🔌 Connecting WebSocket to %s:%d...", gateway.ip, gateway.port)
    async with websockets.connect(
        f"wss://{gateway.ip}:{gateway.port}/ship/",
        ssl=node.ssl_ctx,
        subprotocols=cast(Any, ["ship"]),
        open_timeout=20,
    ) as ws:
        await ws.send(b"\x00\x00")
        ok = await perform_ship_handshake(ws, node.local_ship_id)
        if not ok:
            raise HandshakeFailedError("SHIP handshake failed.")
        logger.debug("🎯 SHIP layer ready!")
        yield Connected(
            ws=ws,
            local_device_address=node.local_device_address,
            gateway=gateway,
        )
