"""Parsers for the SPINE Measurement feature."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..spine import SpineAddress
from ._common import coerce_list, measurement_scope, scaled_number_to_float, unit_to_str

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedMeasurement:
    """One decoded ``measurementListData`` row.

    The parser guarantees the invariants the handler used to re-check by hand:
    ``measurement_id`` is an int, ``value`` a float, ``source`` a real
    :class:`SpineAddress`, and the string fields are always strings (their
    fallbacks, e.g. ``scope_type`` → ``"unknown"``, are applied here).
    """

    measurement_id: int
    value: float
    source: SpineAddress
    scope_type: str = "unknown"
    unit: str = ""
    measurement_type: str = ""


def parse_measurement_description(cmd: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """Parse measurementDescriptionListData → {measurementId: {scopeType, unit, measurementType}}."""
    desc_map: Dict[int, Dict[str, Any]] = {}

    mdl_list = coerce_list(cmd, "measurementDescriptionListData", "measurementDescriptionData")
    if mdl_list is None:
        logger.warning(
            "[MEASUREMENT] measurementDescriptionListData unexpected shape: %r",
            cmd.get("measurementDescriptionListData"),
        )
        return desc_map

    for entry in mdl_list:
        if not isinstance(entry, dict):
            continue
        mid = entry.get("measurementId")
        if not isinstance(mid, int):
            continue
        desc_map[mid] = {
            "scopeType": entry.get("scopeType"),
            "unit": entry.get("unit"),
            "measurementType": entry.get("measurementType"),
        }

    return desc_map


def parse_measurement_list(
    cmd: Dict[str, Any],
    desc_map: Dict[int, Dict[str, Any]],
    *,
    source_address: Optional[Dict[str, Any]] = None,
) -> List[ParsedMeasurement]:
    """Parse measurementListData → list of :class:`ParsedMeasurement`."""

    ml_list = coerce_list(cmd, "measurementListData", "measurementData")
    if ml_list is None:
        logger.warning(
            "[MEASUREMENT] measurementListData unexpected shape: %r",
            cmd.get("measurementListData"),
        )
        return []

    updates: List[ParsedMeasurement] = []

    source = SpineAddress.from_raw(source_address) or SpineAddress()

    for entry in ml_list:
        if not isinstance(entry, dict):
            continue

        mid = entry.get("measurementId")
        mdata = entry.get("measurementData")
        if not isinstance(mid, int):
            continue

        # Prefer the canonical shape: entry.measurementData.value (scaledNumber).
        val = None
        if isinstance(mdata, dict):
            val = scaled_number_to_float(mdata.get("value"))
        # Fallbacks: some devices may inline value.
        if val is None:
            val = scaled_number_to_float(entry.get("value"))
        if val is None:
            continue

        meta = desc_map.get(mid, {})
        scope = measurement_scope(meta.get("scopeType"))
        mtype = meta.get("measurementType") or ""

        updates.append(
            ParsedMeasurement(
                measurement_id=mid,
                value=val,
                source=source,
                scope_type=scope,
                unit=unit_to_str(meta.get("unit")) or "",
                measurement_type=mtype if isinstance(mtype, str) else str(mtype),
            )
        )

    if not updates and ml_list:
        try:
            sample = json.dumps(ml_list[:3], indent=2, ensure_ascii=False)
            logger.warning("[MEASUREMENT] No values parsed; sample entries:\n%s", sample)
        except Exception:
            pass

    return updates
