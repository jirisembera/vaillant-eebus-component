"""mDNS service discovery for SHIP devices.

We both:
- Advertise our own `_ship._tcp.local.` service so the Vaillant app can discover us
  (required for the trust/pairing flow).
- Listen for the gateway's `_ship._tcp.local.` service to find its IP/port.
"""

from __future__ import annotations

import asyncio
import logging
import socket

from zeroconf import ServiceListener
from zeroconf.asyncio import AsyncServiceInfo

logger = logging.getLogger(__name__)

# The Vaillant gateway advertises identity TXT keys (brand/model/type) that our
# bare SHIP clients never set, e.g.:
#   {brand: Vaillant, model: VR921, type: Gateway, register: false, ski, id, ...}
# Positive-match on the brand so we only ever connect to a real Vaillant device
# — other SHIP nodes on the LAN (our own CLI, an HA bridge) are ignored without
# having to enumerate them. Browsing every interface (the default) otherwise
# surfaces those peers and we'd try to connect to one.
GATEWAY_BRAND = "vaillant"


def _txt(properties: dict, key: bytes) -> str:
    """Decode a zeroconf TXT value to ``str`` (values arrive as bytes)."""
    value = properties.get(key)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return ""
    return value or ""


class MDNSHandler(ServiceListener):
    """Collect every Vaillant gateway `_ship._tcp.local.` service seen.

    Pure discovery — it keeps all Vaillant gateways (positively identified by
    TXT ``brand``) in :attr:`gateways`, keyed by SKI, and leaves any selection
    (first found, a specific SKI, …) to the caller. Non-Vaillant SHIP nodes on
    the LAN (our own CLI/HA bridge announcements, other clients) are ignored.
    """

    def __init__(self):
        self.gateways: dict[str, AsyncServiceInfo] = {}

    def add_service(self, zc, type, name):
        asyncio.ensure_future(self.async_add_service(zc, type, name))

    async def async_add_service(self, zc, type, name):
        info = await zc.async_get_service_info(type, name)
        if not info:
            return
        # Positive identification — only Vaillant gateways are connection targets.
        if _txt(info.properties, b"brand").lower() != GATEWAY_BRAND:
            return
        ski = _txt(info.properties, b"ski")
        if ski:
            self.gateways[ski] = info

    def remove_service(self, zc, type, name):
        pass

    def update_service(self, zc, type, name):
        pass


def local_ipv4_addresses() -> list:
    """All non-loopback local IPv4 addresses.

    Used to advertise our own ``_ship._tcp.local.`` service on every interface
    when no specific bind address was requested — the A-record analogue of
    zeroconf's ``InterfaceChoice.All``. mDNS answers each interface with the
    address(es) reachable on it, so a host with multiple interfaces is covered
    without the caller pinning one. ``ifaddr`` is a zeroconf dependency, so it
    is always available.
    """
    import ifaddr

    out = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if ip.is_IPv4 and isinstance(ip.ip, str) and not ip.ip.startswith("127."):
                out.append(ip.ip)
    return out


def resolve_interface_ipv4(name: str) -> str:
    """Resolve a Linux network interface name (e.g. `eth0`) to its IPv4 address."""
    import fcntl
    import struct

    SIOCGIFADDR = 0x8915
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = fcntl.ioctl(
            sock.fileno(),
            SIOCGIFADDR,
            struct.pack("256s", name[:15].encode("utf-8")),
        )
        return socket.inet_ntoa(packed[20:24])
    finally:
        sock.close()
