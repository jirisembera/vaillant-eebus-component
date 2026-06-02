"""pair.py — one-shot trust handshake with an EEBUS gateway.

Runs :class:`vaillant_eebus.pairing.EebusPairing`. While the SHIP handshake holds
``connectionHello`` in ``pending`` you must press *Trust* in the myVAILLANT
app to confirm the new peer. Once the cert is trusted, the resulting
``cert.pem``/``key.pem`` carry the trust forward and the ``eebus_to_mqtt`` bridge
can run without further interaction.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from cli import (
    build_common_parser,
    resolve_bind_address,
    resolve_log_level,
    setup_logging,
)
from vaillant_eebus import EebusError, discover_gateway, open_node
from vaillant_eebus.pairing import EebusPairing


async def _pair(*, bind_address, mdns_timeout, mdns_port, cert_file, key_file, target_ski):
    async with open_node(
        cert_file=cert_file,
        key_file=key_file,
        bind_address=bind_address,
        mdns_port=mdns_port,
    ) as node:
        gateway = await discover_gateway(
            aiozc=node.aiozc, mdns_timeout=mdns_timeout, target_ski=target_ski
        )
        return await EebusPairing(gateway, node=node).run()


def main(argv: "list[str] | None" = None) -> int:
    parser = build_common_parser(
        prog="vaillant-eebus-pair",
        description=(
            "One-shot SHIP pairing handshake. Hold this command running while "
            "you accept the trust prompt in the myVAILLANT app."
        ),
    )
    args = parser.parse_args(argv)
    level, enable_trace = resolve_log_level(args)
    setup_logging(level, args.log_format, enable_trace)

    bind_address = resolve_bind_address(args)

    try:
        gateway = asyncio.run(
            _pair(
                bind_address=bind_address,
                mdns_timeout=args.mdns_timeout,
                mdns_port=args.mdns_port,
                cert_file=args.cert_file,
                key_file=args.key_file,
                target_ski=args.ski,
            )
        )
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("👋 Interrupted by user")
        return 130
    except EebusError as e:
        logging.getLogger("vaillant-eebus-pair").error("❌ %s", e)
        return 1

    log = logging.getLogger("vaillant-eebus-pair")
    log.info("🎉 Paired with gateway at %s:%d (SKI=%s)", gateway.ip, gateway.port, gateway.ski)
    log.info("Cert files: %s / %s", args.cert_file, args.key_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
