"""Parsers for the SPINE Setpoint feature."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..spine import SpineAddress
from ._common import coerce_list, scaled_number_to_float


@dataclass(frozen=True)
class ParsedSetpoint:
    """One decoded ``setpointListData`` row.

    ``setpoint_id`` is an int and ``value`` a float (the parser drops rows
    missing either); ``source`` is always a real :class:`SpineAddress`. The
    description-derived strings stay ``Optional`` — the key/label fallbacks live
    in :mod:`vaillant_eebus.keys` / :mod:`vaillant_eebus.naming`.
    """

    setpoint_id: int
    value: float
    source: SpineAddress
    setpoint_type: Optional[str] = None
    scope_type: Optional[str] = None
    unit: Optional[str] = None


def parse_setpoint_descriptions(cmd: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """{setpointId: {setpointType, scope, unit}}."""
    out: Dict[int, Dict[str, Any]] = {}
    items = coerce_list(cmd, "setpointDescriptionListData", "setpointDescriptionData")
    if items is None:
        return out
    for entry in items:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("setpointId")
        if not isinstance(sid, int):
            continue
        out[sid] = {
            "setpointType": entry.get("setpointType"),
            "scopeType": entry.get("scopeType"),
            "unit": entry.get("unit"),
            "description": entry.get("description"),
        }
    return out


def parse_setpoint_constraints(cmd: Dict[str, Any]) -> Dict[int, Dict[str, Optional[float]]]:
    """{setpointId: {rangeMin, rangeMax, stepSize}} from setpointConstraintsListData.

    The gateway reports per-setpoint limits as SPINE scaled numbers
    (``setpointRangeMin`` / ``setpointRangeMax`` / ``setpointStepSize``), e.g. DHW
    ``setpointRangeMin={number:35,scale:0}`` → ``35.0``. Each field is optional —
    a row may carry only a subset (or, for an unused setpoint id, none at all).
    """
    out: Dict[int, Dict[str, Optional[float]]] = {}
    items = coerce_list(cmd, "setpointConstraintsListData", "setpointConstraintsData")
    if items is None:
        return out
    for entry in items:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("setpointId")
        if not isinstance(sid, int):
            continue
        out[sid] = {
            "rangeMin": scaled_number_to_float(entry.get("setpointRangeMin")),
            "rangeMax": scaled_number_to_float(entry.get("setpointRangeMax")),
            "stepSize": scaled_number_to_float(entry.get("setpointStepSize")),
        }
    return out


def parse_setpoint_list(
    cmd: Dict[str, Any],
    desc_map: Dict[int, Dict[str, Any]],
    *,
    source_address: Optional[Dict[str, Any]] = None,
) -> List[ParsedSetpoint]:
    """Parse setpointListData → list of :class:`ParsedSetpoint`."""
    out: List[ParsedSetpoint] = []
    items = coerce_list(cmd, "setpointListData", "setpointData")
    if items is None:
        return out
    source = SpineAddress.from_raw(source_address) or SpineAddress()
    for entry in items:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("setpointId")
        if not isinstance(sid, int):
            continue
        # Setpoint value can sit at entry.value or entry.setpointValue depending on stack version.
        val = scaled_number_to_float(entry.get("value"))
        if val is None:
            val = scaled_number_to_float(entry.get("setpointValue"))
        if val is None:
            continue
        meta = desc_map.get(sid, {})
        out.append(
            ParsedSetpoint(
                setpoint_id=sid,
                value=val,
                source=source,
                setpoint_type=meta.get("setpointType"),
                scope_type=meta.get("scopeType"),
                unit=meta.get("unit"),
            )
        )
    return out
