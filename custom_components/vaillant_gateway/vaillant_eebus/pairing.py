"""First-time pairing helper.

The SHIP handshake holds ``connectionHello: pending`` until the user accepts
the trust prompt in the myVAILLANT app. :class:`EebusPairing` runs exactly
that flow and exits: it does not subscribe to features or start the SPINE
loop. After a successful pairing the certificate files referenced by
``cert_file`` / ``key_file`` carry the trust forward and ordinary
:class:`vaillant_eebus.client.EebusClient` runs can use them.
"""

from __future__ import annotations

import logging

from .connection import Gateway, LocalNode, open_connection

logger = logging.getLogger(__name__)


class EebusPairing:
    """One-shot pairing handshake against an already-discovered gateway.

    Usage::

        async with open_node() as node:
            gw = await discover_gateway(aiozc=node.aiozc)
            await EebusPairing(gw, node=node).run()
    """

    def __init__(self, gateway: Gateway, *, node: LocalNode) -> None:
        self._gateway = gateway
        self._node = node

    async def run(self) -> Gateway:
        """Run pairing. Returns the gateway info once trust is established.

        Raises:
            :class:`vaillant_eebus.errors.HandshakeFailedError`
                when the user aborts the trust prompt or the SHIP handshake
                otherwise fails.
        """
        async with open_connection(self._gateway, node=self._node) as conn:
            logger.info(
                "✅ Paired with gateway at %s:%d (SKI=%s)",
                conn.gateway.ip,
                conn.gateway.port,
                conn.gateway.ski,
            )
            return conn.gateway
