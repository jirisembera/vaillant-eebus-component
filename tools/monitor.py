"""``vaillant-eebus-monitor`` CLI: stream live updates from a Vaillant gateway.

Connects to the gateway via :class:`vaillant_eebus.client.EebusClient` and prints
each :class:`~vaillant_eebus.events.Update` as it arrives — the diagnostic
counterpart to the HA MQTT bridge (no MQTT). For a one-shot topology snapshot
instead, see :mod:`tools/info.py`.

    python3 tools/monitor.py [-a IPv4 | -i IFACE] [--mdns-timeout N]
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
from vaillant_eebus.errors import GatewayNotFoundError
from vaillant_eebus.events import (
    HvacModeUpdate,
    HvacOverrunUpdate,
    MeasurementUpdate,
    SetpointUpdate,
    Update,
)
from vaillant_eebus.naming import (
    friendly_hvac_function_name,
    friendly_overrun_name,
    friendly_sensor_name,
    friendly_setpoint_name,
    measurement_emoji,
    unit_to_ha,
)


def format_update(update: Update) -> str:
    """Format an :class:`Update` as a one-line human-readable string."""
    if isinstance(update, MeasurementUpdate):
        name = friendly_sensor_name(
            update.scope_type,
            source_entity=update.source_entity,
            phase_info=update.phase,
        )
        unit = update.unit or unit_to_ha(update.unit) or ""
        return f"{measurement_emoji(update.scope_type)} {name}: {update.value} {unit}".rstrip()
    if isinstance(update, SetpointUpdate):
        name = friendly_setpoint_name(update.scope_type)
        unit = update.unit or "°C"
        return f"🎯 {name}: {update.value} {unit}".rstrip()
    if isinstance(update, HvacModeUpdate):
        name = friendly_hvac_function_name(
            update.system_function_type,
            system_function_id=update.system_function_id,
        )
        return f"🔧 {name}: {update.mode if update.mode else 'unknown'}"
    if isinstance(update, HvacOverrunUpdate):
        name = friendly_overrun_name(update.overrun_type)
        return f"⏱️ {name}: {'active' if update.active else 'inactive'}"
    return f"{update.key}: {update.value}"


async def _run_print_updates(
    *,
    bind_address: Optional[str],
    mdns_timeout: int,
    mdns_port: int,
    cert_file: str,
    key_file: str,
    target_ski: Optional[str] = None,
) -> None:
    from vaillant_eebus.client import EebusClient
    from vaillant_eebus.connection import discover_gateway, open_node

    async with open_node(
        cert_file=cert_file,
        key_file=key_file,
        bind_address=bind_address,
        mdns_port=mdns_port,
    ) as node:
        gateway = await discover_gateway(
            aiozc=node.aiozc, mdns_timeout=mdns_timeout, target_ski=target_ski
        )
        async with EebusClient(gateway, node=node) as hp:
            await hp.start(subscribe=True, read_values=True)
            log = logging.getLogger("vaillant_eebus")
            # start(read_values=True) read the initial values into hp.values; print
            # that snapshot, then stream live notifications (updates() is live-only).
            for update in hp.values.values():
                log.info("%s", format_update(update))
            async for update in hp.updates():
                log.info("%s", format_update(update))


def main(argv: "list[str] | None" = None) -> int:
    """Standalone entry point: connect and print each received update."""
    parser = build_common_parser(
        prog="vaillant-eebus-monitor",
        description="Connect to a Vaillant EEBUS gateway and stream updates.",
    )
    args = parser.parse_args(argv)
    level, enable_trace = resolve_log_level(args)
    setup_logging(level, args.log_format, enable_trace)

    bind_address = resolve_bind_address(args)

    try:
        asyncio.run(
            _run_print_updates(
                bind_address=bind_address,
                mdns_timeout=args.mdns_timeout,
                mdns_port=args.mdns_port,
                cert_file=args.cert_file,
                key_file=args.key_file,
                target_ski=args.ski,
            )
        )
    except GatewayNotFoundError as e:
        logging.getLogger("vaillant_eebus").error("❌ %s", e)
        return 1
    except KeyboardInterrupt:
        logging.getLogger("vaillant-eebus-monitor").info("👋 Interrupted by user")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
