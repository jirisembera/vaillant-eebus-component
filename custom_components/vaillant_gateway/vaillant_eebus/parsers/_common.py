"""Shared helpers used across the per-feature SPINE parsers."""

from __future__ import annotations

from typing import Any, Dict, Optional


def scaled_number_to_float(v: Any) -> Optional[float]:
    """Convert a SPINE scaledNumber {number, scale} into a Python float."""
    if not isinstance(v, dict):
        return None
    number = v.get("number")
    scale = v.get("scale", 0)
    if not isinstance(number, int):
        return None
    if not isinstance(scale, int):
        scale = 0
    try:
        return float(number) * (10.0 ** float(scale))
    except Exception:
        return None


def float_to_scaled_number(value: float, scale: int = -1) -> Dict[str, int]:
    """Inverse of :func:`scaled_number_to_float`.

    Encodes a Python float as a SPINE ``{"number": int, "scale": int}`` pair.
    Default ``scale=-1`` (one decimal place) matches the encoding the gateway
    uses for DHW/room setpoints (e.g. ``48.0 °C`` → ``{"number": 480, "scale": -1}``).
    """
    number = int(round(value * (10.0 ** float(-scale))))
    return {"number": number, "scale": scale}


def unit_to_str(unit: Any) -> Optional[str]:
    """Extract a plain unit token from a SPINE unit value.

    SPINE reports a unit either as a bare token (``"degC"``, ``"W"``) or wrapped
    as ``{"unit": ...}`` / ``{"name": ...}``. Return the stripped token, or
    ``None`` when nothing usable is present. Display normalization (e.g.
    ``degC`` → ``°C``) is layered on top by :func:`vaillant_eebus.naming.unit_to_ha`.
    """
    if isinstance(unit, str):
        return unit.strip() or None
    if isinstance(unit, dict):
        for k in ("unit", "name"):
            inner = unit.get(k)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return None


def str_or_none(v: Any) -> Optional[str]:
    """Return a non-empty, stripped string, or ``None`` for anything else."""
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def measurement_scope(scope_type: Optional[str]) -> str:
    """Effective scope label for a measurement (``scopeType`` → ``"unknown"``).

    The single fallback shared by the Measurement parser and
    :func:`vaillant_eebus.keys.measurement_key`, so a value's emitted
    ``scope_type`` and its stable key always agree.
    """
    return scope_type or "unknown"


def setpoint_scope(scope_type: Optional[str], setpoint_type: Optional[str]) -> str:
    """Effective scope label for a setpoint (``scopeType`` → ``setpointType`` → ``"setpoint"``).

    The single fallback shared by the Setpoint handler and
    :func:`vaillant_eebus.keys.setpoint_key`.
    """
    return scope_type or setpoint_type or "setpoint"


def coerce_list(cmd: Dict[str, Any], primary_key: str, *fallback_keys: str) -> Optional[list]:
    """Return the inner list under `primary_key`, tolerating array-wrapped variants."""
    val = cmd.get(primary_key)
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        for k in (primary_key, *fallback_keys):
            inner = val.get(k)
            if isinstance(inner, list):
                return inner
    return None
