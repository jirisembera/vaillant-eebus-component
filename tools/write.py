"""Set writable values on a Vaillant EEBUS gateway.

Run as::

    python3 tools/write.py                     # list writable items, exit
    python3 tools/write.py <key> <value>       # write that value
    python3 tools/write.py <key> <value> --no-confirm   # skip read-back

``<key>`` is a stable :class:`vaillant_eebus.events.SetpointUpdate` /
:class:`vaillant_eebus.events.HvacModeUpdate` /
:class:`vaillant_eebus.events.HvacOverrunUpdate` key (the ``✏️`` marker on each
writable item in ``python3 tools/info.py`` shows the key to use). The script
detects what the key targets from its prefix (``setpoint_…`` vs ``hvacmode_…``
vs ``hvacoverrun_…``).

For setpoints, ``<value>`` is parsed as a float (degrees Celsius).
For HVAC modes, ``<value>`` is either the ``operationModeType`` name
(e.g. ``"heating"``) or its integer id.
For boost / overruns (e.g. the one-time DHW charge), ``<value>`` is ``on`` /
``off`` (also accepts active/inactive, true/false, 1/0).

The companion ``python3 tools/info.py`` script is read-only — use it first
to find the key you want to write.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import List, Optional

from cli import (
    build_common_parser,
    resolve_bind_address,
    resolve_log_level,
    setup_logging,
)
from vaillant_eebus.events import HvacModeUpdate, HvacOverrunUpdate, SetpointUpdate, Update

_SETPOINT_PREFIX = "setpoint_"
_HVAC_PREFIX = "hvacmode_"
_OVERRUN_PREFIX = "hvacoverrun_"

# Accepted on/off spellings for a boost / overrun write.
_OVERRUN_ON = {"on", "active", "true", "1", "yes", "start"}
_OVERRUN_OFF = {"off", "inactive", "false", "0", "no", "stop", "cancel"}


def _key_kind(key: str) -> str:
    """Return ``'setpoint'`` / ``'hvac'`` / ``'overrun'`` based on the key prefix."""
    if key.startswith(_SETPOINT_PREFIX):
        return "setpoint"
    if key.startswith(_HVAC_PREFIX):
        return "hvac"
    if key.startswith(_OVERRUN_PREFIX):
        return "overrun"
    raise ValueError(
        f"Unrecognised key prefix in {key!r}; expected one starting with "
        f"{_SETPOINT_PREFIX!r}, {_HVAC_PREFIX!r} or {_OVERRUN_PREFIX!r}."
    )


@dataclass
class _Writable:
    """The writable updates the gateway exposed, split by kind for listing/lookup."""

    setpoints: List[SetpointUpdate]
    hvac_modes: List[HvacModeUpdate]
    overruns: List[HvacOverrunUpdate]


def _split_writable(values: List[Update]) -> _Writable:
    writable = _Writable(
        setpoints=[u for u in values if isinstance(u, SetpointUpdate)],
        hvac_modes=[u for u in values if isinstance(u, HvacModeUpdate)],
        overruns=[u for u in values if isinstance(u, HvacOverrunUpdate)],
    )
    writable.setpoints.sort(key=lambda u: u.key)
    writable.hvac_modes.sort(key=lambda u: u.key)
    writable.overruns.sort(key=lambda u: u.key)
    return writable


def _print_listing(values: List[Update]) -> None:
    writable = _split_writable(values)
    if not (writable.setpoints or writable.hvac_modes or writable.overruns):
        print(
            "No writable items cached. Run `python3 tools/info.py` first to confirm "
            "the gateway exposed any Setpoint / HVAC features."
        )
        return
    printed = False
    if writable.setpoints:
        print("Setpoints (write value as °C):")
        for u in writable.setpoints:
            label = u.scope_type or u.setpoint_type or "(setpoint)"
            unit = u.unit or "°C"
            print(f"  {u.key}")
            print(f"    type:    {label}")
            print(f"    current: {u.value} {unit}")
        printed = True
    if writable.hvac_modes:
        if printed:
            print()
        print("HVAC modes (write the operationModeType name or its int id):")
        for u in writable.hvac_modes:
            label = u.system_function_type or "(system function)"
            print(f"  {u.key}")
            print(f"    type:    {label}")
            print(f"    current: {u.mode if u.mode else '(unknown)'}")
        printed = True
    if writable.overruns:
        if printed:
            print()
        print("Boost / overruns (write on/off):")
        for u in writable.overruns:
            label = u.overrun_type or "(overrun)"
            print(f"  {u.key}")
            print(f"    type:    {label}")
            print(f"    current: {'active' if u.active else 'inactive'}")


def _parse_setpoint_value(raw: str) -> float:
    try:
        return float(raw)
    except ValueError as e:
        raise SystemExit(f"Setpoint value must be a number, got {raw!r}") from e


def _parse_hvac_value(raw: str):
    raw = raw.strip()
    if not raw:
        raise SystemExit("HVAC mode value must be non-empty")
    if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
        return int(raw)
    return raw


def _parse_overrun_value(raw: str) -> bool:
    token = raw.strip().lower()
    if token in _OVERRUN_ON:
        return True
    if token in _OVERRUN_OFF:
        return False
    raise SystemExit(
        f"Boost / overrun value must be on/off (also active/inactive, true/false, 1/0), got {raw!r}"
    )


async def _do_list(
    *,
    bind_address: Optional[str],
    mdns_timeout: int,
    mdns_port: int,
    cert_file: str,
    key_file: str,
    overall_timeout: float,
    target_ski: Optional[str] = None,
) -> int:
    from contextlib import AsyncExitStack

    from vaillant_eebus.client import EebusClient
    from vaillant_eebus.connection import discover_gateway, open_node

    async with AsyncExitStack() as stack:
        node = await stack.enter_async_context(
            open_node(
                cert_file=cert_file,
                key_file=key_file,
                bind_address=bind_address,
                mdns_port=mdns_port,
            )
        )
        gateway = await discover_gateway(
            aiozc=node.aiozc, mdns_timeout=mdns_timeout, target_ski=target_ski
        )
        hp = await stack.enter_async_context(EebusClient(gateway, node=node))

        async def _do() -> List[Update]:
            await hp.start(read_values=True)
            return list(hp.values.values())

        values = await asyncio.wait_for(_do(), timeout=overall_timeout)
    _print_listing(values)
    return 0


async def _do_write(
    *,
    bind_address: Optional[str],
    mdns_timeout: int,
    mdns_port: int,
    cert_file: str,
    key_file: str,
    overall_timeout: float,
    write_timeout: float,
    key: str,
    raw_value: str,
    confirm: bool,
    target_ski: Optional[str] = None,
) -> int:
    from contextlib import AsyncExitStack

    from vaillant_eebus.client import EebusClient
    from vaillant_eebus.connection import discover_gateway, open_node

    log = logging.getLogger("vaillant_eebus.write")
    kind = _key_kind(key)

    async with AsyncExitStack() as stack:
        node = await stack.enter_async_context(
            open_node(
                cert_file=cert_file,
                key_file=key_file,
                bind_address=bind_address,
                mdns_port=mdns_port,
            )
        )
        gateway = await discover_gateway(
            aiozc=node.aiozc, mdns_timeout=mdns_timeout, target_ski=target_ski
        )
        hp = await stack.enter_async_context(EebusClient(gateway, node=node))

        async def _do() -> int:
            await hp.start(read_values=True)

            before = hp.values.get(key)
            if before is None:
                available = sorted(
                    k
                    for k, v in hp.values.items()
                    if isinstance(v, (SetpointUpdate, HvacModeUpdate, HvacOverrunUpdate))
                )
                log.error("❌ Key %r not found. Available keys:", key)
                for k in available:
                    log.error("    %s", k)
                return 2

            if kind == "setpoint":
                if not isinstance(before, SetpointUpdate):
                    log.error(
                        "❌ Key %r exists but is not a SetpointUpdate (got %s).",
                        key,
                        type(before).__name__,
                    )
                    return 2
                value = _parse_setpoint_value(raw_value)
                unit = before.unit or "°C"
                log.info(
                    "✏️ Writing setpoint %s: %s %s → %s %s", key, before.value, unit, value, unit
                )
                await hp.write_setpoint(key, value, timeout=write_timeout)
            elif kind == "hvac":
                if not isinstance(before, HvacModeUpdate):
                    log.error(
                        "❌ Key %r exists but is not an HvacModeUpdate (got %s).",
                        key,
                        type(before).__name__,
                    )
                    return 2
                mode = _parse_hvac_value(raw_value)
                log.info("✏️ Writing HVAC mode %s: %s → %s", key, before.mode, mode)
                await hp.write_hvac_mode(key, mode, timeout=write_timeout)
            else:  # overrun
                if not isinstance(before, HvacOverrunUpdate):
                    log.error(
                        "❌ Key %r exists but is not an HvacOverrunUpdate (got %s).",
                        key,
                        type(before).__name__,
                    )
                    return 2
                active = _parse_overrun_value(raw_value)
                log.info(
                    "✏️ Writing boost %s: %s → %s",
                    key,
                    "active" if before.active else "inactive",
                    "active" if active else "inactive",
                )
                await hp.write_overrun(key, active, timeout=write_timeout)

            log.info("✅ Write acknowledged by gateway.")

            if confirm:
                await hp.read_values()
                after = hp.values.get(key)
                if isinstance(after, SetpointUpdate):
                    log.info("📥 Read-back: %s = %s %s", key, after.value, after.unit or "°C")
                elif isinstance(after, HvacModeUpdate):
                    log.info("📥 Read-back: %s = %s", key, after.mode)
                elif kind == "overrun":
                    # The overrun state isn't part of the value re-read (it rides the
                    # description/auxiliary path, not value_cmd_keys), so read_values()
                    # can't refresh it here. Observe it live with `tools/monitor.py`,
                    # or re-read it with a fresh `tools/info.py`.
                    log.info("📥 Boost write acknowledged; re-run tools/info.py to re-read state.")
            return 0

        return await asyncio.wait_for(_do(), timeout=overall_timeout)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_common_parser(
        prog="vaillant-eebus-write",
        description=(
            "Set a writable value on a Vaillant EEBUS gateway. "
            "Run without positional arguments to list writable items + keys."
        ),
    )
    parser.add_argument(
        "key",
        nargs="?",
        help=(
            "Stable key of the item to write. Setpoint keys start with "
            "'setpoint_', HVAC mode keys with 'hvacmode_', boost/overrun keys "
            "with 'hvacoverrun_'. Find them in the ✏️ markers printed by "
            "`python3 tools/info.py`."
        ),
    )
    parser.add_argument(
        "value",
        nargs="?",
        help=(
            "Value to write. For setpoints: a number (°C). For HVAC modes: "
            "the operationModeType name (e.g. 'heating') or its int id. For "
            "boost/overruns: on/off (also active/inactive, true/false, 1/0)."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Overall seconds budget for the whole run (default: 30).",
    )
    parser.add_argument(
        "--write-timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for the gateway's result reply (default: 5).",
    )
    parser.add_argument(
        "--no-confirm",
        dest="confirm",
        action="store_false",
        default=True,
        help="Skip the post-write read-back.",
    )

    args = parser.parse_args(argv)

    if (args.key is None) != (args.value is None):
        parser.error("Provide both <key> and <value>, or neither (to list writable items).")

    level, enable_trace = resolve_log_level(args)
    setup_logging(level, args.log_format, enable_trace)

    bind_address = resolve_bind_address(args)

    log = logging.getLogger("vaillant_eebus.write")
    try:
        if args.key is None:
            return asyncio.run(
                _do_list(
                    bind_address=bind_address,
                    mdns_timeout=args.mdns_timeout,
                    mdns_port=args.mdns_port,
                    cert_file=args.cert_file,
                    key_file=args.key_file,
                    overall_timeout=args.timeout,
                    target_ski=args.ski,
                )
            )
        return asyncio.run(
            _do_write(
                bind_address=bind_address,
                mdns_timeout=args.mdns_timeout,
                mdns_port=args.mdns_port,
                cert_file=args.cert_file,
                key_file=args.key_file,
                overall_timeout=args.timeout,
                write_timeout=args.write_timeout,
                key=args.key,
                raw_value=args.value,
                confirm=args.confirm,
                target_ski=args.ski,
            )
        )
    except KeyboardInterrupt:
        log.info("👋 Interrupted by user")
        return 130
    except asyncio.TimeoutError:
        log.error("⏰ Operation timed out after %.1fs", args.timeout)
        return 1
    except ValueError as e:
        log.error("❌ %s", e)
        return 2
    except Exception as e:
        log.error("❌ Write failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
