"""Parsers for the SPINE HVAC feature."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..spine import SpineAddress
from ._common import coerce_list


@dataclass(frozen=True)
class ParsedSystemFunction:
    """One decoded ``hvacSystemFunctionListData`` row.

    ``system_function_id`` is an int (rows without one are dropped) and
    ``source`` is always a real :class:`SpineAddress`;
    ``current_operation_mode_id`` is ``None`` when the gateway omitted it.
    ``is_operation_mode_changeable`` / ``is_overrun_active`` are ``None`` when the
    gateway omitted the flag (it carries them on this row alongside the mode).
    """

    system_function_id: int
    source: SpineAddress
    current_operation_mode_id: Optional[int] = None
    is_operation_mode_changeable: Optional[bool] = None
    is_overrun_active: Optional[bool] = None


@dataclass(frozen=True)
class ParsedOverrun:
    """One decoded ``hvacOverrunListData`` row.

    ``overrun_id`` is an int (rows without one are dropped) and ``source`` is
    always a real :class:`SpineAddress`. ``status`` is the raw SPINE string
    (``"active"`` / ``"inactive"``) or ``None`` when omitted.
    """

    overrun_id: int
    source: SpineAddress
    status: Optional[str] = None


def parse_hvac_operation_mode_descriptions(cmd: Dict[str, Any]) -> Dict[int, str]:
    """{operationModeId: operationModeType} (e.g. 1 → 'heating', 2 → 'cooling')."""
    out: Dict[int, str] = {}
    items = coerce_list(
        cmd, "hvacOperationModeDescriptionListData", "hvacOperationModeDescriptionData"
    )
    if items is None:
        return out
    for entry in items:
        if not isinstance(entry, dict):
            continue
        oid = entry.get("operationModeId")
        otype = entry.get("operationModeType")
        if isinstance(oid, int) and isinstance(otype, str):
            out[oid] = otype
    return out


def parse_hvac_system_function_descriptions(cmd: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """{systemFunctionId: {systemFunctionType, operationModeIds, ...}}."""
    out: Dict[int, Dict[str, Any]] = {}
    items = coerce_list(
        cmd, "hvacSystemFunctionDescriptionListData", "hvacSystemFunctionDescriptionData"
    )
    if items is None:
        return out
    for entry in items:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("systemFunctionId")
        if not isinstance(sid, int):
            continue
        out[sid] = {
            "systemFunctionType": entry.get("systemFunctionType"),
            "operationModeIds": entry.get("operationModeIds"),
            "description": entry.get("description"),
        }
    return out


def parse_hvac_system_function_setpoint_relations(
    cmd: Dict[str, Any],
) -> Dict[int, Dict[int, List[int]]]:
    """{systemFunctionId: {operationModeId: [setpointId, ...]}}.

    Maps each operation mode to the setpoint(s) it selects — e.g. ``eco`` → [3],
    ``on`` → [1], ``auto`` → [1, 3]. Lets a setpoint be tied back to the mode
    that distinctively uses it. ``off`` (no setpoints) yields an empty list.
    """
    out: Dict[int, Dict[int, List[int]]] = {}
    items = coerce_list(
        cmd,
        "hvacSystemFunctionSetpointRelationListData",
        "hvacSystemFunctionSetpointRelationData",
    )
    if items is None:
        return out
    for entry in items:
        if not isinstance(entry, dict):
            continue
        sf = entry.get("systemFunctionId")
        mode = entry.get("operationModeId")
        if not isinstance(sf, int) or not isinstance(mode, int):
            continue
        raw = entry.get("setpointId")
        if isinstance(raw, list):
            sids = [int(x) for x in raw if isinstance(x, int)]
        elif isinstance(raw, int):
            sids = [raw]
        else:
            sids = []
        out.setdefault(sf, {})[mode] = sids
    return out


def parse_hvac_system_function_list(
    cmd: Dict[str, Any],
    *,
    source_address: Optional[Dict[str, Any]] = None,
) -> List[ParsedSystemFunction]:
    """Parse hvacSystemFunctionListData → list of :class:`ParsedSystemFunction`."""
    out: List[ParsedSystemFunction] = []
    items = coerce_list(cmd, "hvacSystemFunctionListData", "hvacSystemFunctionData")
    if items is None:
        return out
    source = SpineAddress.from_raw(source_address) or SpineAddress()
    for entry in items:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("systemFunctionId")
        cur = entry.get("currentOperationModeId")
        if not isinstance(sid, int):
            continue
        changeable = entry.get("isOperationModeIdChangeable")
        overrun = entry.get("isOverrunActive")
        out.append(
            ParsedSystemFunction(
                system_function_id=sid,
                source=source,
                current_operation_mode_id=cur if isinstance(cur, int) else None,
                is_operation_mode_changeable=changeable if isinstance(changeable, bool) else None,
                is_overrun_active=overrun if isinstance(overrun, bool) else None,
            )
        )
    return out


def parse_hvac_overrun_descriptions(cmd: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """{overrunId: {overrunType, affectedSystemFunctionId: [int, ...]}}.

    Describes each overrun the HVAC feature offers — e.g. the one-time DHW
    charge (``overrunType=oneTimeDhw``) that the myVAILLANT app exposes as a
    hot-water boost.
    """
    out: Dict[int, Dict[str, Any]] = {}
    items = coerce_list(cmd, "hvacOverrunDescriptionListData", "hvacOverrunDescriptionData")
    if items is None:
        return out
    for entry in items:
        if not isinstance(entry, dict):
            continue
        oid = entry.get("overrunId")
        if not isinstance(oid, int):
            continue
        raw = entry.get("affectedSystemFunctionId")
        if isinstance(raw, list):
            affected = [int(x) for x in raw if isinstance(x, int)]
        elif isinstance(raw, int):
            affected = [raw]
        else:
            affected = []
        out[oid] = {
            "overrunType": entry.get("overrunType"),
            "affectedSystemFunctionId": affected,
        }
    return out


def parse_hvac_overrun_list(
    cmd: Dict[str, Any],
    *,
    source_address: Optional[Dict[str, Any]] = None,
) -> List[ParsedOverrun]:
    """Parse hvacOverrunListData → list of :class:`ParsedOverrun`."""
    out: List[ParsedOverrun] = []
    items = coerce_list(cmd, "hvacOverrunListData", "hvacOverrunData")
    if items is None:
        return out
    source = SpineAddress.from_raw(source_address) or SpineAddress()
    for entry in items:
        if not isinstance(entry, dict):
            continue
        oid = entry.get("overrunId")
        if not isinstance(oid, int):
            continue
        status = entry.get("overrunStatus")
        out.append(
            ParsedOverrun(
                overrun_id=oid,
                source=source,
                status=status if isinstance(status, str) else None,
            )
        )
    return out
