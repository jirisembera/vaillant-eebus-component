"""``eebus-to-mqtt`` CLI: run the EEBUS comm client and bridge updates to HA MQTT."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

from cli import (
    build_common_parser,
    resolve_bind_address,
    resolve_log_level,
    setup_logging,
)
from vaillant_eebus import EebusClient, EebusError, discover_gateway, open_node

from .bridge import HABridge


async def _run(
    *,
    bind_address: Optional[str],
    mdns_timeout: int,
    mdns_port: int,
    cert_file: str,
    key_file: str,
    target_ski: Optional[str] = None,
) -> None:
    async with open_node(
        cert_file=cert_file,
        key_file=key_file,
        bind_address=bind_address,
        mdns_port=mdns_port,
    ) as node:
        gateway = await discover_gateway(
            aiozc=node.aiozc, mdns_timeout=mdns_timeout, target_ski=target_ski
        )
        async with EebusClient(gateway, node=node) as client, HABridge(client=client):
            await client.start(subscribe=True, read_values=True)
            await client.wait_closed()


def main(argv: "list[str] | None" = None) -> int:
    parser = build_common_parser(
        prog="eebus-to-mqtt",
        description=(
            "Connect to a Vaillant EEBUS gateway and republish telemetry "
            "to Home Assistant via MQTT Discovery."
        ),
    )
    args = parser.parse_args(argv)
    level, enable_trace = resolve_log_level(args)
    setup_logging(level, args.log_format, enable_trace)

    bind_address = resolve_bind_address(args)

    try:
        asyncio.run(
            _run(
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
    except (EebusError, RuntimeError) as e:
        logging.getLogger(__name__).error("❌ %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
