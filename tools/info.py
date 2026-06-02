"""Diagnostic snapshot CLI for a Vaillant EEBUS gateway.

Connects to the gateway, runs discovery + descriptions (no SPINE subscriptions),
optionally polls the current values, then prints the device topology as a
hierarchical tree before exiting. Useful for "what does this gateway expose?"
diagnostics — the counterpart to :mod:`tools/monitor.py`, which streams updates
indefinitely.

Run as ``python3 tools/info.py [-a IPv4 | -i IFACE] [-vv]``.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Dict, Iterable, List, Optional, Tuple

from cli import (
    build_common_parser,
    resolve_bind_address,
    resolve_log_level,
    setup_logging,
)
from vaillant_eebus.events import (
    HvacModeUpdate,
    HvacOverrunUpdate,
    MeasurementUpdate,
    SetpointUpdate,
    Update,
)
from vaillant_eebus.naming import (
    friendly_hvac_function_name,
    friendly_sensor_name,
    friendly_setpoint_name,
    measurement_emoji,
    phase_label,
    unit_to_ha,
)
from vaillant_eebus.topology import (
    EntityInfo,
    FeatureInfo,
    Topology,
)

# Preferred display order for feature types within an entity.
_FEATURE_ORDER = ("Measurement", "Setpoint", "HVAC", "ElectricalConnection")


def _feature_sort_key(f: FeatureInfo) -> Tuple[int, int]:
    try:
        rank = _FEATURE_ORDER.index(f.feature_type)
    except ValueError:
        rank = len(_FEATURE_ORDER)
    return (rank, f.feature)


# ---------------------------------------------------------------------------
# Value lookups: pair description items with the corresponding current value
# ---------------------------------------------------------------------------


def _index_updates(values: Iterable[Update]) -> Dict[str, Update]:
    """Index updates by their stable key for description-driven tree lookup.

    Each topology description carries the same stable key (see
    :mod:`vaillant_eebus.keys`), so a description pairs with its current value by
    a single ``value_index.get(desc.key)`` — no ``(kind, entity, feature, id)``
    join.
    """
    return {u.key: u for u in values}


# ---------------------------------------------------------------------------
# One-line formatters for each item type
# ---------------------------------------------------------------------------


def _format_measurement_line(
    feature: FeatureInfo,
    md,
    update: Optional[MeasurementUpdate],
) -> str:
    name = friendly_sensor_name(
        md.scope_type or "",
        source_entity=feature.entity,
        phase_info=update.phase if isinstance(update, MeasurementUpdate) else None,
    )
    unit = unit_to_ha(md.unit) if md.unit else ""
    head = f"{measurement_emoji(md.scope_type or '')} id={md.measurement_id} {name}"
    if md.measurement_type and md.measurement_type.lower() != "real":
        head += f" [{md.measurement_type}]"
    if update is not None:
        val_unit = update.unit or unit
        return f"{head} = {update.value} {val_unit}".rstrip()
    if unit:
        return f"{head} ({unit})"
    return head


def _format_setpoint_line(
    sd,
    update: Optional[SetpointUpdate],
    entity_type: Optional[str] = None,
) -> str:
    name = friendly_setpoint_name(
        sd.scope_type or "", entity_type=entity_type, mode_type=sd.mode_type
    )
    unit = unit_to_ha(sd.unit) if sd.unit else "°C"
    head = f"🎯 id={sd.setpoint_id} {name}"
    if sd.description:
        head += f' — "{sd.description}"'
    if sd.range_min is not None or sd.range_max is not None or sd.step is not None:
        lo = "?" if sd.range_min is None else sd.range_min
        hi = "?" if sd.range_max is None else sd.range_max
        bounds = f"[{lo}–{hi}"
        if sd.step is not None:
            bounds += f" step {sd.step}"
        head += f" {bounds}]"
    if update is not None:
        val_unit = update.unit or unit
        line = f"{head} = {update.value} {val_unit}".rstrip()
    else:
        line = f"{head} ({unit})"
    return f"{line}  ✏️ {sd.key}"


def _format_hvac_line(
    sf,
    update: Optional[HvacModeUpdate],
    entity_type: Optional[str] = None,
) -> str:
    name = friendly_hvac_function_name(
        sf.system_function_type or "",
        system_function_id=sf.system_function_id,
        entity_type=entity_type,
    )
    head = f"🔧 sf={sf.system_function_id} {name}"
    if sf.system_function_type:
        head += f" [{sf.system_function_type}]"
    if update is not None and update.mode:
        line = f"{head} = {update.mode}"
    else:
        line = head
    if isinstance(update, HvacModeUpdate):
        flags = []
        if update.mode_changeable is not None:
            flags.append(f"changeable={update.mode_changeable}")
        if update.overrun_active is not None:
            flags.append(f"boost={update.overrun_active}")
        if flags:
            line += f" ({', '.join(flags)})"
    return f"{line}  ✏️ {sf.key}"


def _format_overrun_line(ov, update: Optional[HvacOverrunUpdate]) -> str:
    head = f"⏱️ ov={ov.overrun_id} {ov.overrun_type or 'overrun'}"
    if update is not None:
        head += f" = {'active' if update.active else 'inactive'}"
    return f"{head}  ✏️ {ov.key}"


def _format_electrical_line(_feature: FeatureInfo, ep) -> str:
    bits = [f"id={ep.measurement_id}"]
    if ep.phases:
        bits.append(f"phase={phase_label(ep.phases) or ep.phases}")
    if ep.measurement_type:
        bits.append(f"type={ep.measurement_type}")
    if ep.variant:
        bits.append(f"variant={ep.variant}")
    if ep.voltage_type:
        bits.append(f"voltage={ep.voltage_type}")
    if ep.scope_type:
        bits.append(f"scope={ep.scope_type}")
    return "🔌 " + " ".join(bits)


# ---------------------------------------------------------------------------
# Tree rendering
# ---------------------------------------------------------------------------


def _entity_header(ent: EntityInfo) -> str:
    head = f"Entity {list(ent.entity)}  {ent.entity_type or '(unknown type)'}"
    if ent.description:
        head += f' — "{ent.description}"'
    return head


def _feature_header(f: FeatureInfo) -> str:
    head = f"{f.feature_type} (feature {f.feature}"
    if f.role and f.role != "server":
        head += f", role={f.role}"
    return head + ")"


def _feature_lines(
    feature: FeatureInfo,
    value_index: Dict[str, Update],
    entity_type: Optional[str] = None,
) -> List[str]:
    out: List[str] = []
    ft = feature.feature_type
    if ft == "Measurement":
        for md in feature.measurements:
            up = value_index.get(md.key)
            out.append(
                _format_measurement_line(
                    feature, md, up if isinstance(up, MeasurementUpdate) else None
                )
            )
    elif ft == "Setpoint":
        for sd in feature.setpoints:
            up = value_index.get(sd.key)
            out.append(
                _format_setpoint_line(
                    sd, up if isinstance(up, SetpointUpdate) else None, entity_type
                )
            )
    elif ft == "HVAC":
        for sf in feature.system_functions:
            up = value_index.get(sf.key)
            out.append(
                _format_hvac_line(sf, up if isinstance(up, HvacModeUpdate) else None, entity_type)
            )
        if feature.operation_modes:
            modes = ", ".join(
                f"{m.operation_mode_id}={m.operation_mode_type}" for m in feature.operation_modes
            )
            out.append(f"⚙ operation modes: {modes}")
        for ov in feature.overruns:
            up = value_index.get(ov.key)
            out.append(_format_overrun_line(ov, up if isinstance(up, HvacOverrunUpdate) else None))
    elif ft == "ElectricalConnection":
        for ep in feature.electrical_parameters:
            out.append(_format_electrical_line(feature, ep))
    return out


class _Node:
    """A label plus its sub-nodes — a uniform tree the connector renderer walks.

    Entities, features and individual value lines all collapse to this, so one
    ``_draw`` handles the arbitrary entity nesting that ``EntityInfo.children``
    now exposes (HVACRoom under HeatingZone under HeatingCircuit, …).
    """

    __slots__ = ("label", "children")

    def __init__(self, label: str, children: Iterable["_Node"] = ()) -> None:
        self.label = label
        self.children = list(children)


def _feature_node(
    feature: FeatureInfo,
    value_index: Dict[str, Update],
    entity_type: Optional[str],
) -> _Node:
    items = _feature_lines(feature, value_index, entity_type)
    leaves = [_Node(item) for item in items] or [_Node("(no description items)")]
    return _Node(_feature_header(feature), leaves)


def _entity_node(ent: EntityInfo, value_index: Dict[str, Update]) -> _Node:
    features = [
        _feature_node(f, value_index, ent.entity_type)
        for f in sorted(ent.features, key=_feature_sort_key)
    ]
    sub_entities = [_entity_node(c, value_index) for c in ent.children]
    children = features + sub_entities or [_Node("(no server features)")]
    return _Node(_entity_header(ent), children)


def _draw(node: _Node, prefix: str, is_last: bool, out: List[str]) -> None:
    out.append(f"{prefix}{'└─' if is_last else '├─'} {node.label}")
    child_prefix = prefix + ("   " if is_last else "│  ")
    for i, child in enumerate(node.children):
        _draw(child, child_prefix, i == len(node.children) - 1, out)


def render_topology(topology: Topology, updates: Iterable[Update]) -> List[str]:
    """Render the topology as a hierarchical list of printable lines.

    Entities nest under their parents (walking ``Topology.roots()`` →
    ``EntityInfo.children``), each carrying its server features and current
    values, so the printed tree mirrors the device's real structure.
    """
    value_index = _index_updates(updates)
    lines: List[str] = [f"📡 Device: {topology.device_address or '(unknown device)'}"]

    roots = topology.roots()
    if not roots:
        lines.append("   (no entities discovered)")
        return lines

    for i, ent in enumerate(roots):
        _draw(_entity_node(ent, value_index), "", i == len(roots) - 1, lines)
    return lines


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


async def _gather(
    *,
    bind_address: Optional[str],
    mdns_timeout: int,
    mdns_port: int,
    cert_file: str,
    key_file: str,
    overall_timeout: float,
    read_values: bool,
    target_ski: Optional[str] = None,
) -> Tuple[Optional[Topology], List[Update]]:
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

            async def _do() -> Tuple[Optional[Topology], List[Update]]:
                await hp.start(subscribe=False, read_values=read_values)
                return hp.topology, list(hp.values.values())

            return await asyncio.wait_for(_do(), timeout=overall_timeout)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_common_parser(
        prog="vaillant-eebus-info",
        description=(
            "Snapshot a Vaillant EEBUS gateway: connect, run discovery + "
            "descriptions, optionally poll values, print the topology as a tree, exit."
        ),
    )
    snap = parser.add_argument_group("snapshot")
    snap.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Overall seconds budget for the snapshot (default: 30).",
    )
    snap.add_argument(
        "--no-values",
        dest="read_values",
        action="store_false",
        default=True,
        help="Skip the one-shot value read; only print descriptions.",
    )
    args = parser.parse_args(argv)

    level, enable_trace = resolve_log_level(args)
    setup_logging(level, args.log_format, enable_trace)

    bind_address = resolve_bind_address(args)

    log = logging.getLogger("vaillant_eebus.info")
    try:
        topology, values = asyncio.run(
            _gather(
                bind_address=bind_address,
                mdns_timeout=args.mdns_timeout,
                mdns_port=args.mdns_port,
                cert_file=args.cert_file,
                key_file=args.key_file,
                overall_timeout=args.timeout,
                read_values=args.read_values,
                target_ski=args.ski,
            )
        )
    except KeyboardInterrupt:
        log.info("👋 Interrupted by user")
        return 130
    except asyncio.TimeoutError:
        log.error("⏰ Snapshot timed out after %.1fs", args.timeout)
        return 1
    except Exception as e:
        log.error("❌ Connection failed: %s", e)
        return 1

    if topology is None:
        log.error("❌ Snapshot returned no topology (discovery never completed)")
        return 1

    for line in render_topology(topology, values):
        print(line)

    return 0


if __name__ == "__main__":
    sys.exit(main())
