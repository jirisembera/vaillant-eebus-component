"""``vaillant-eebus-discover`` CLI: list the Vaillant EEBUS gateways visible via mDNS.

Read-only — browses ``_ship._tcp.local.`` on a throwaway private zeroconf and
prints a table (no pairing cert, no announcement, no connection). Replaces the
old ``vaillant_eebus --list-gateways`` flag.

    python3 tools/discover.py [-a IPv4 | -i IFACE] [--mdns-timeout N]
"""

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
from vaillant_eebus.connection import discover_gateways, private_aiozc


async def _discover(bind_address: Optional[str], timeout: int) -> list:
    """Browse for gateways on a throwaway private zeroconf, then close it."""
    async with private_aiozc(bind_address) as aiozc:
        return await discover_gateways(aiozc=aiozc, timeout=timeout)


def _print_table(gateways: list) -> None:
    headers = ("MODEL", "ADDRESS", "SKI", "DEVICE ID")
    rows = [(g.model or "EEBUS", f"{g.ip}:{g.port}", g.ski, g.name) for g in gateways]
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))]

    def line(cols) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cols)).rstrip()

    n = len(gateways)
    print(f"🔎 Discovered {n} Vaillant gateway{'' if n == 1 else 's'}:\n")
    print(line(headers))
    print(line(tuple("─" * w for w in widths)))
    for r in rows:
        print(line(r))


def main(argv: "list[str] | None" = None) -> int:
    parser = build_common_parser(
        prog="vaillant-eebus-discover",
        description="List the Vaillant EEBUS gateways visible via mDNS, then exit.",
        ship_options=False,
    )
    # Listing always waits the whole window (no early exit), so default to a
    # short scan; an explicit --mdns-timeout still wins.
    parser.set_defaults(mdns_timeout=5)
    args = parser.parse_args(argv)

    level, enable_trace = resolve_log_level(args)
    setup_logging(level, args.log_format, enable_trace)

    bind_address = resolve_bind_address(args)

    gateways = asyncio.run(_discover(bind_address, timeout=args.mdns_timeout))
    if not gateways:
        logging.getLogger("vaillant_eebus.discover").warning(
            "No Vaillant EEBUS gateways found on the network."
        )
        return 1
    _print_table(gateways)
    return 0


if __name__ == "__main__":
    sys.exit(main())
