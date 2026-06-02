"""Parsers for the SPINE ElectricalConnection feature."""

from __future__ import annotations

from typing import Any, Dict

from ._common import coerce_list


def parse_electrical_param_descriptions(cmd: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """{measurementId: {phases, measurementType, variant, scopeType, parameterId, voltageType}}.

    Links Measurement IDs to electrical metadata (per-phase / line-to-line).
    """
    out: Dict[int, Dict[str, Any]] = {}
    items = coerce_list(
        cmd,
        "electricalConnectionParameterDescriptionListData",
        "electricalConnectionParameterDescriptionData",
    )
    if items is None:
        return out
    for entry in items:
        if not isinstance(entry, dict):
            continue
        mid = entry.get("measurementId")
        pid = entry.get("parameterId")
        if not isinstance(mid, int):
            continue
        out[mid] = {
            "parameterId": pid,
            "phases": entry.get("acMeasuredPhases"),
            "measurementType": entry.get("acMeasurementType"),
            "variant": entry.get("acMeasurementVariant"),
            "voltageType": entry.get("voltageType"),
            "scopeType": entry.get("scopeType"),
        }
    return out
