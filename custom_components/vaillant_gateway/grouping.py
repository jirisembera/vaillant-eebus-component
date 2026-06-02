"""Decide which SPINE entities collapse into ``climate`` / ``water_heater``.

The rule is structural: an entity is a grouping candidate when it carries a
temperature measurement, at least one setpoint, *and* an HVAC system function
that all have a current readout (key present in ``client.values``).
``dhw``/``domestichotwater`` system functions become ``water_heater`` entities;
everything else (heating, cooling, heat-pump combos) becomes ``climate``.

The plan keeps the raw temperature ``sensor`` visible even when grouped, so
``grouped_setpoint_keys`` / ``grouped_hvac_keys`` only suppress the matching
``number`` / ``select``. Temperature and HVAC-text sensors are unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional, Set, Tuple

from .vaillant_eebus import (
    EntityInfo,
    FeatureInfo,
    Topology,
)

EntityAddr = Tuple[int, ...]


@dataclass(frozen=True)
class ClimateGroup:
    entity: EntityAddr
    temperature_key: str
    setpoint_key: str
    hvac_key: str
    hvac_feature: FeatureInfo  # carries operation_modes for hvac_modes / presets
    name: Optional[str] = None  # device user_label (e.g. zone name) or None


@dataclass(frozen=True)
class WaterHeaterGroup:
    entity: EntityAddr
    temperature_key: str
    setpoint_key: str
    hvac_key: str
    hvac_feature: FeatureInfo
    name: Optional[str] = None  # device user_label or None


@dataclass(frozen=True)
class Grouping:
    climate_entities: Tuple[ClimateGroup, ...] = ()
    water_heater_entities: Tuple[WaterHeaterGroup, ...] = ()
    grouped_setpoint_keys: FrozenSet[str] = field(default_factory=frozenset)
    grouped_hvac_keys: FrozenSet[str] = field(default_factory=frozenset)


def _pick_first_temperature_key(
    feature: FeatureInfo,
    value_keys: FrozenSet[str],
) -> Optional[str]:
    """Return the precomputed key of a temperature measurement under ``feature``.

    Each description already carries the stable key of the value it emits, so a
    current readout is just ``desc.key in value_keys`` — no scan of the live
    update stream needed.
    """
    for desc in feature.measurements:
        if desc.key in value_keys and "temperature" in (desc.scope_type or "").lower():
            return desc.key
    return None


def _pick_first_setpoint_key(
    feature: FeatureInfo,
    value_keys: FrozenSet[str],
) -> Optional[str]:
    """Return the lowest-id setpoint key with a current readout under ``feature``."""
    matching = [
        (desc.setpoint_id, desc.key) for desc in feature.setpoints if desc.key in value_keys
    ]
    if not matching:
        return None
    matching.sort()
    return matching[0][1]


def _pick_first_hvac_key(
    feature: FeatureInfo,
    value_keys: FrozenSet[str],
) -> Optional[str]:
    """Return the lowest-id HVAC system-function key with a readout under ``feature``."""
    matching = [
        (desc.system_function_id, desc.key)
        for desc in feature.system_functions
        if desc.key in value_keys
    ]
    if not matching:
        return None
    matching.sort()
    return matching[0][1]


def _is_dhw(entity: EntityInfo, feature: FeatureInfo) -> bool:
    # The device-reported entity type is the most direct signal; the system
    # function's own type (carried on the description) is the fallback.
    if (entity.entity_type or "").lower() == "dhwcircuit":
        return True
    for desc in feature.system_functions:
        sf_type = (desc.system_function_type or "").lower()
        if "dhw" in sf_type or "domestichotwater" in sf_type:
            return True
    return False


def _features_by_type(entity: EntityInfo) -> Dict[str, list[FeatureInfo]]:
    out: Dict[str, list[FeatureInfo]] = {}
    for f in entity.features:
        out.setdefault(f.feature_type, []).append(f)
    return out


def compute_grouping(
    topology: Topology,
    value_keys: FrozenSet[str],
) -> Grouping:
    """Classify each entity in ``topology`` into climate / water_heater / none.

    Driven entirely by the topology: each description carries the stable key of
    the value it emits, so ``key in value_keys`` decides whether an item has a
    current readout. No live update stream is needed.
    """
    climate: list[ClimateGroup] = []
    water: list[WaterHeaterGroup] = []
    sp_keys: Set[str] = set()
    hvac_keys: Set[str] = set()

    for entity in topology.entities:
        ft = _features_by_type(entity)
        meas_features = ft.get("Measurement", [])
        setpt_features = ft.get("Setpoint", [])
        hvac_features = ft.get("HVAC", [])
        if not (meas_features and setpt_features and hvac_features):
            continue

        # Pick the first temperature readout across this entity's measurement features.
        temp_key: Optional[str] = None
        for mf in meas_features:
            temp_key = _pick_first_temperature_key(mf, value_keys)
            if temp_key:
                break
        if temp_key is None:
            continue

        # Pick the first setpoint readout across this entity's setpoint features.
        sp_key: Optional[str] = None
        for sf in setpt_features:
            sp_key = _pick_first_setpoint_key(sf, value_keys)
            if sp_key:
                break
        if sp_key is None:
            continue

        # Pick the first HVAC mode readout; require operation_modes to be present
        # (otherwise the writable side of a climate/water_heater is meaningless).
        hvac_key: Optional[str] = None
        hvac_feat: Optional[FeatureInfo] = None
        for hf in hvac_features:
            if not hf.operation_modes:
                continue
            candidate = _pick_first_hvac_key(hf, value_keys)
            if candidate:
                hvac_key = candidate
                hvac_feat = hf
                break
        if hvac_key is None or hvac_feat is None:
            continue

        # Name the rich entity after the owner's app label when the device
        # provides one (resolved from the nearest ancestor entity), else leave
        # None so the platform applies its generic default.
        name = topology.nearest_user_label(entity.entity)
        if _is_dhw(entity, hvac_feat):
            water.append(
                WaterHeaterGroup(
                    entity=entity.entity,
                    temperature_key=temp_key,
                    setpoint_key=sp_key,
                    hvac_key=hvac_key,
                    hvac_feature=hvac_feat,
                    name=name,
                )
            )
        else:
            climate.append(
                ClimateGroup(
                    entity=entity.entity,
                    temperature_key=temp_key,
                    setpoint_key=sp_key,
                    hvac_key=hvac_key,
                    hvac_feature=hvac_feat,
                    name=name,
                )
            )
        sp_keys.add(sp_key)
        hvac_keys.add(hvac_key)

    return Grouping(
        climate_entities=tuple(climate),
        water_heater_entities=tuple(water),
        grouped_setpoint_keys=frozenset(sp_keys),
        grouped_hvac_keys=frozenset(hvac_keys),
    )
