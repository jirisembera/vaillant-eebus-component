"""Friendly labels for SPINE telemetry.

The mapping from raw SPINE scope/entity to human-readable names is shared
between the diagnostic CLI and the Home Assistant bridge, so it lives in the
comm library. Anything HA-specific (device_class, state_class) stays in the
``eebus_to_mqtt`` package.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from .parsers import unit_to_str

# ---------------------------------------------------------------------------
# Unit normalization
# ---------------------------------------------------------------------------

_UNIT_DISPLAY = {"degC": "°C", "degF": "°F"}


def unit_to_ha(unit: Any) -> str:
    """Normalize SPINE unit representations to display-friendly strings.

    Extracts the bare token (:func:`vaillant_eebus.parsers.unit_to_str` handles
    both ``"degC"`` and ``{"unit": "degC"}``), then maps the temperature tokens
    to ``°C`` / ``°F`` and leaves everything else as-is.
    """
    token = unit_to_str(unit)
    if token is None:
        return ""
    return _UNIT_DISPLAY.get(token, token)


# ---------------------------------------------------------------------------
# Phase labels (ElectricalConnection)
# ---------------------------------------------------------------------------


_PHASE_LABELS = {
    "a": "L1",
    "b": "L2",
    "c": "L3",
    "abc": "Σ",
    "ab": "L1-L2",
    "bc": "L2-L3",
    "ca": "L3-L1",
    "ac": "L1-L3",
}


def phase_label(phases: Optional[str]) -> str:
    if not isinstance(phases, str):
        return ""
    return _PHASE_LABELS.get(phases.strip().lower(), phases.strip().upper())


# ---------------------------------------------------------------------------
# Entity purpose (device-provided)
# ---------------------------------------------------------------------------

# DeviceClassification / discovery entity types → coarse purpose. This is what
# classifies DHW vs heating/cooling when the scope/system-function type alone is
# inconclusive — replacing the old "entity [4] = DHW, entity [5,…] = heating"
# entity-address guesswork (now removed) with the device's own entity type.
_ENTITY_KIND = {
    "dhwcircuit": "dhw",
    "hvacroom": "heating",
    "heatingzone": "heating",
    "heatingcircuit": "heating",
    "coolingcircuit": "cooling",
    "coolingzone": "cooling",
}


def _entity_kind(entity_type: Optional[str]) -> Optional[str]:
    if not isinstance(entity_type, str):
        return None
    return _ENTITY_KIND.get(entity_type.strip().lower())


# ---------------------------------------------------------------------------
# Friendly names
# ---------------------------------------------------------------------------


def friendly_sensor_name(
    scope_type: str,
    *,
    source_entity: Optional[Iterable[int]] = None,
    phase_info: Optional[Dict[str, Any]] = None,
) -> str:
    """Return a human-friendly sensor name.

    ``phase_info`` (when present) comes from
    ElectricalConnectionParameterDescription and lets us label per-phase
    electrical measurements (L1/L2/L3 etc.).
    """
    s = (scope_type or "").strip()
    low = s.lower()
    ent_list = list(source_entity) if source_entity is not None else None

    phase = ""
    meas_type = ""
    if phase_info:
        phase = phase_label(phase_info.get("phases"))
        mt = phase_info.get("measurementType")
        if isinstance(mt, str) and mt and mt.lower() != "real":
            meas_type = f" {mt}"
    suffix = (f" {phase}" if phase else "") + meas_type

    if low == "outsideairtemperature":
        return "Outdoor Temperature"
    if low == "dhwtemperature":
        return "DHW Temperature"
    if low == "roomairtemperature":
        return "Room Temperature"
    if low == "acpowertotal":
        return "Compressor Power Total"
    if low == "acpower":
        return f"Compressor Power{suffix}"
    if low.startswith("acpower"):
        return f"Power{suffix}"
    if low == "acvoltage":
        return f"Voltage{suffix}"
    if low == "accurrent":
        return f"Current{suffix}"
    if low == "acfrequency":
        return "Grid Frequency"
    if low == "acenergyconsumed":
        return "Energy Consumed (cumulative)"
    if low == "acenergyproduced":
        return "Energy Exported (cumulative)"
    if "temperature" in low:
        return "Temperature"

    if ent_list:
        return f"{s} (entity={ent_list})"
    return s or "Measurement"


# Operation-mode types that meaningfully name a *setpoint variant* (vs. the
# scheduling modes on/off/auto, which add no qualifier). Sourced from the HVAC
# mode↔setpoint relation list.
_SETPOINT_MODE_QUALIFIER = {
    "eco": "Eco",
    "comfort": "Comfort",
    "reduced": "Reduced",
    "night": "Night",
    "day": "Day",
}


def _base_setpoint_noun(scope_type: Any, entity_type: Optional[str]) -> Optional[str]:
    """The noun a setpoint is *about* ("DHW", "Room Temperature", …), or None."""
    s = (str(scope_type) if scope_type else "").lower()
    if "dhw" in s:
        return "DHW"
    if "room" in s:
        return "Room Temperature"
    if "outside" in s or "outdoor" in s:
        return "Outdoor Temperature"
    if "flow" in s or "supply" in s:
        return "Flow Temperature"
    if "return" in s:
        return "Return Temperature"
    kind = _entity_kind(entity_type)
    if kind == "dhw":
        return "DHW"
    if kind in ("heating", "cooling"):
        return "Room Temperature"
    return None


def friendly_setpoint_name(
    scope_type: Any,
    *,
    entity_type: Optional[str] = None,
    mode_type: Optional[str] = None,
    user_label: Optional[str] = None,
) -> str:
    """Human-readable label for a setpoint.

    Assembles ``{noun} {Eco?} Setpoint``. The noun is the owner's app
    ``user_label`` (e.g. a heating-zone name) when given, else a device-provided
    scope/entity-type noun. ``mode_type`` is the distinctive HVAC operation mode
    for this setpoint (from the mode↔setpoint relation list); known variants like
    ``eco`` add a qualifier, while scheduling modes (on/off/auto) add nothing.
    Falls back to a generic label rather than guessing from the address layout.
    """
    qualifier = _SETPOINT_MODE_QUALIFIER.get((str(mode_type) if mode_type else "").strip().lower())
    label = user_label.strip() if isinstance(user_label, str) and user_label.strip() else None
    noun = label or _base_setpoint_noun(scope_type, entity_type)
    if noun:
        parts = [noun] + ([qualifier] if qualifier else []) + ["Setpoint"]
        return " ".join(parts)
    if qualifier:
        return f"{qualifier} Setpoint"
    return f"Setpoint ({scope_type})" if scope_type else "Setpoint"


def friendly_hvac_function_name(
    system_function_type: Any,
    *,
    system_function_id: Optional[int] = None,
    entity_type: Optional[str] = None,
) -> str:
    """Human-readable label for an HVAC system function.

    Both inputs are device-provided: ``system_function_type`` is the primary
    signal, and the entity type (e.g. ``DHWCircuit`` / ``HVACRoom``) classifies
    it when the function type is inconclusive. When neither resolves, returns a
    generic label rather than guessing from the entity-address layout.
    """
    s = (str(system_function_type) if system_function_type else "").lower()
    if "dhw" in s or "domestichotwater" in s:
        return "DHW Mode"
    if "heatingcooling" in s or "heatpump" in s:
        return "Heating/Cooling Mode"
    if "heating" in s:
        return "Heating Mode"
    if "cooling" in s:
        return "Cooling Mode"
    kind = _entity_kind(entity_type)
    if kind == "dhw":
        return "DHW Mode"
    if kind == "heating":
        return "Heating/Cooling Mode"
    if kind == "cooling":
        return "Cooling Mode"
    if system_function_id is not None:
        return f"Operating Mode (sf{system_function_id})"
    return "Operating Mode"


def friendly_overrun_name(
    overrun_type: Any,
    *,
    entity_type: Optional[str] = None,
) -> str:
    """Human-readable label for an HVAC overrun (a temporary forced run).

    ``oneTimeDhw`` is the hot-water boost (a one-time cylinder charge); other
    overrun types fall back to a generic "Boost" qualified by the device's
    entity kind when known.
    """
    s = (str(overrun_type) if overrun_type else "").lower()
    if "dhw" in s or "domestichotwater" in s:
        return "DHW Boost"
    if _entity_kind(entity_type) == "dhw":
        return "DHW Boost"
    return "Boost"


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def measurement_emoji(scope_type: str) -> str:
    low = (scope_type or "").lower()
    if "dhw" in low:
        return "🚿"
    if "power" in low or "energy" in low:
        return "⚡"
    if "voltage" in low or "current" in low or "frequency" in low:
        return "🔌"
    if "temperature" in low:
        return "🌡️"
    return "📊"
