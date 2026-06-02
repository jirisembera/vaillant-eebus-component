"""Structured representation of the gateway device topology.

A :class:`Topology` snapshot is assembled from the gateway's
``nodeManagementDetailedDiscoveryData`` reply plus the per-feature
description data captured by the SPINE handlers. It exposes the device's
entities, their server features, and the descriptions for each feature
class (Measurement / HVAC / Setpoint / ElectricalConnection).

Available via :attr:`vaillant_eebus.EebusClient.topology` once
:meth:`vaillant_eebus.EebusClient.start` has completed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .keys import hvac_mode_key, hvac_overrun_key, measurement_key, setpoint_key
from .parsers import entity_addr_list, extract_entities, str_or_none, unit_to_str
from .spine import FeatureKey


@dataclass(frozen=True)
class MeasurementDescription:
    measurement_id: int
    scope_type: Optional[str] = None
    measurement_type: Optional[str] = None
    unit: Optional[str] = None
    # Stable update key of this item's value — equals the key of the
    # MeasurementUpdate the gateway will emit (see :mod:`vaillant_eebus.keys`).
    key: str = ""


@dataclass(frozen=True)
class SetpointDescription:
    setpoint_id: int
    scope_type: Optional[str] = None
    setpoint_type: Optional[str] = None
    unit: Optional[str] = None
    description: Optional[str] = None
    key: str = ""
    # The HVAC operation mode that distinctively selects this setpoint (e.g.
    # "eco"), derived from hvacSystemFunctionSetpointRelationListData when the
    # entity has more than one referenced setpoint. None when not disambiguating.
    mode_type: Optional[str] = None
    # Device-reported writable bounds from setpointConstraintsListData (already
    # decoded to floats). None where the gateway didn't report that limit; the
    # HA entities fall back to scope/entity-type defaults when so.
    range_min: Optional[float] = None
    range_max: Optional[float] = None
    step: Optional[float] = None


@dataclass(frozen=True)
class HvacSystemFunctionDescription:
    system_function_id: int
    system_function_type: Optional[str] = None
    key: str = ""


@dataclass(frozen=True)
class HvacOperationModeDescription:
    operation_mode_id: int
    operation_mode_type: str


@dataclass(frozen=True)
class HvacOverrunDescription:
    overrun_id: int
    overrun_type: Optional[str] = None
    affected_system_function_ids: Tuple[int, ...] = ()
    # Stable update key of this overrun's state (equals the HvacOverrunUpdate key).
    key: str = ""


@dataclass(frozen=True)
class ElectricalParameterDescription:
    measurement_id: int
    parameter_id: Optional[int] = None
    phases: Optional[str] = None
    measurement_type: Optional[str] = None
    variant: Optional[str] = None
    voltage_type: Optional[str] = None
    scope_type: Optional[str] = None


@dataclass(frozen=True)
class FeatureInfo:
    entity: Tuple[int, ...]
    feature: int
    feature_type: str
    role: str
    measurements: Tuple[MeasurementDescription, ...] = ()
    setpoints: Tuple[SetpointDescription, ...] = ()
    system_functions: Tuple[HvacSystemFunctionDescription, ...] = ()
    operation_modes: Tuple[HvacOperationModeDescription, ...] = ()
    overruns: Tuple[HvacOverrunDescription, ...] = ()
    electrical_parameters: Tuple[ElectricalParameterDescription, ...] = ()


@dataclass(frozen=True)
class EntityInfo:
    entity: Tuple[int, ...]
    entity_type: Optional[str] = None
    description: Optional[str] = None
    features: Tuple[FeatureInfo, ...] = ()
    # Direct child entities, by SPINE address nesting (e.g. HeatingZone [5, 1] is
    # a child of HeatingCircuit [5]). Built so the same EntityInfo objects appear
    # both here and in the flat ``Topology.entities`` index; walk down from
    # ``Topology.roots()`` through this to traverse the whole device tree.
    children: Tuple["EntityInfo", ...] = ()
    # DeviceClassification metadata (when the entity advertises that feature).
    # ``user_label`` comes from user data (the owner's app label, e.g. a heating
    # zone name); the rest from manufacturer data.
    user_label: Optional[str] = None
    device_name: Optional[str] = None
    device_code: Optional[str] = None
    serial_number: Optional[str] = None
    vendor_name: Optional[str] = None
    brand_name: Optional[str] = None


@dataclass(frozen=True)
class TopologyMaps:
    """The per-feature description maps that enrich a discovery-derived topology.

    Grouped into one object so :func:`build_topology` takes the discovery payload,
    the device address, and this single bundle instead of five loose maps. Each
    field is keyed exactly as the owning handler exposes it (see
    :meth:`vaillant_eebus.client._Handlers.topology_maps`).
    """

    measurements_by_key: Mapping[FeatureKey, Mapping[int, Dict[str, Any]]] = field(
        default_factory=dict
    )
    setpoints_by_key: Mapping[FeatureKey, Mapping[int, Dict[str, Any]]] = field(
        default_factory=dict
    )
    # {(entity, feature): {setpointId: {rangeMin, rangeMax, stepSize}}} — the
    # device-reported writable bounds (floats) from setpointConstraintsListData.
    setpoint_constraints_by_key: Mapping[FeatureKey, Mapping[int, Dict[str, Any]]] = field(
        default_factory=dict
    )
    system_functions_by_key: Mapping[FeatureKey, Mapping[int, Dict[str, Any]]] = field(
        default_factory=dict
    )
    operation_modes_by_key: Mapping[FeatureKey, Mapping[int, str]] = field(default_factory=dict)
    # {(entity, feature): {systemFunctionId: {operationModeId: [setpointId, ...]}}}
    hvac_setpoint_relations_by_key: Mapping[FeatureKey, Mapping[int, Mapping[int, List[int]]]] = (
        field(default_factory=dict)
    )
    # {(entity, feature): {overrunId: {overrunType, affectedSystemFunctionId}}}
    overruns_by_key: Mapping[FeatureKey, Mapping[int, Dict[str, Any]]] = field(default_factory=dict)
    electrical_params_by_entity: Mapping[Tuple[int, ...], Mapping[int, Dict[str, Any]]] = field(
        default_factory=dict
    )
    # {entity: {"manufacturer": {...}, "user": {...}}} from DeviceClassification.
    device_classification_by_entity: Mapping[Tuple[int, ...], Dict[str, Any]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class DeviceIdentity:
    """Device-level identity distilled from DeviceClassification manufacturer data.

    Mapped onto Home Assistant's ``DeviceInfo`` fields by the integration.
    """

    manufacturer: Optional[str] = None
    model: Optional[str] = None
    model_id: Optional[str] = None
    serial: Optional[str] = None
    name: Optional[str] = None


@dataclass(frozen=True)
class Topology:
    device_address: Optional[str] = None
    entities: Tuple[EntityInfo, ...] = ()

    def entity(self, entity: Tuple[int, ...]) -> Optional[EntityInfo]:
        for e in self.entities:
            if e.entity == entity:
                return e
        return None

    def roots(self) -> Tuple[EntityInfo, ...]:
        """Top-level entities — those with no ancestor entity present here.

        Every entity is either a root or reachable through
        :attr:`EntityInfo.children` from exactly one root, so ``roots()`` plus
        ``children`` walks the entire device tree. Returned in the same sorted
        address order as :attr:`entities`.
        """
        present = {e.entity for e in self.entities}
        return tuple(
            e
            for e in self.entities
            if not any(e.entity[:k] in present for k in range(1, len(e.entity)))
        )

    def feature(self, entity: Tuple[int, ...], feature: int) -> Optional[FeatureInfo]:
        ent = self.entity(entity)
        if ent is None:
            return None
        for f in ent.features:
            if f.feature == feature:
                return f
        return None

    def setpoint_description(self, key: str) -> Optional[SetpointDescription]:
        """The :class:`SetpointDescription` whose stable ``key`` matches ``key``.

        Lets a consumer holding only a setpoint's update key (e.g. a grouped
        ``water_heater`` / ``climate`` entity) recover its device-reported
        bounds. Returns ``None`` when no setpoint carries that key.
        """
        for ent in self.entities:
            for f in ent.features:
                for sd in f.setpoints:
                    if sd.key == key:
                        return sd
        return None

    def overrun_description(self, key: str) -> Optional[HvacOverrunDescription]:
        """The :class:`HvacOverrunDescription` whose stable ``key`` matches ``key``.

        Lets a consumer holding only an overrun's update key (e.g. the DHW-boost
        switch) recover its type/affected functions. ``None`` when unmatched.
        """
        for ent in self.entities:
            for f in ent.features:
                for ov in f.overruns:
                    if ov.key == key:
                        return ov
        return None

    def primary_identity(self) -> Optional[DeviceIdentity]:
        """The device's own identity for the registry.

        Prefers the ``HeatPumpAppliance`` entity (which carries the full model /
        serial); otherwise the lowest-address entity that reported any model name
        or serial. Returns ``None`` when no DeviceClassification manufacturer data
        was captured.
        """
        candidates = [
            e for e in self.entities if e.entity_type == "HeatPumpAppliance" and _has_identity(e)
        ]
        if not candidates:
            candidates = sorted(
                (e for e in self.entities if _has_identity(e)), key=lambda e: e.entity
            )
        if not candidates:
            return None
        e = candidates[0]
        return DeviceIdentity(
            manufacturer=e.brand_name or e.vendor_name,
            model=e.device_name,
            model_id=e.device_code,
            serial=e.serial_number,
            name=e.device_name,
        )

    def nearest_user_label(self, entity: Tuple[int, ...]) -> Optional[str]:
        """The nearest ``user_label`` on ``entity`` or one of its ancestors.

        Walks the entity-address prefix outward (``entity``, ``entity[:-1]``, …)
        so a label set on a parent entity (e.g. a HeatingZone) resolves onto a
        groupable child (e.g. its HVACRoom).
        """
        for i in range(len(entity), 0, -1):
            ent = self.entity(entity[:i])
            if ent is not None and ent.user_label:
                return ent.user_label
        return None


def _has_identity(entity: EntityInfo) -> bool:
    return bool(entity.device_name or entity.serial_number)


def _as_float(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _entity_description(desc: Any) -> Optional[str]:
    if isinstance(desc, str):
        return str_or_none(desc)
    if isinstance(desc, dict):
        for k in ("label", "description"):
            v = desc.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _distinctive_setpoint_modes(
    relations_by_key: Mapping[FeatureKey, Mapping[int, Mapping[int, List[int]]]],
    operation_modes_by_key: Mapping[FeatureKey, Mapping[int, str]],
) -> Dict[Tuple[int, ...], Dict[int, str]]:
    """Map ``{entity: {setpointId: operationModeType}}`` for disambiguable setpoints.

    For each HVAC feature, flatten its system functions' mode→setpoint relations.
    Only when ≥2 distinct setpoints are referenced does a mode that selects
    exactly one of them stamp that setpoint with the mode's type.
    """
    out: Dict[Tuple[int, ...], Dict[int, str]] = {}
    for (entity, feature), sf_relations in relations_by_key.items():
        opmodes = operation_modes_by_key.get((entity, feature), {})
        mode_to_setpoints: Dict[int, List[int]] = {}
        for mode_map in sf_relations.values():
            for mode_id, sids in mode_map.items():
                bucket = mode_to_setpoints.setdefault(mode_id, [])
                for s in sids:
                    if s not in bucket:
                        bucket.append(s)
        referenced = {s for sids in mode_to_setpoints.values() for s in sids}
        if len(referenced) < 2:
            continue
        for mode_id, sids in mode_to_setpoints.items():
            mtype = opmodes.get(mode_id)
            if len(sids) == 1 and isinstance(mtype, str):
                out.setdefault(entity, {})[sids[0]] = mtype
    return out


def build_topology(
    *,
    discovery: Optional[Dict[str, Any]],
    device_address: Optional[str] = None,
    maps: Optional[TopologyMaps] = None,
) -> Topology:
    """Compose a :class:`Topology` from a discovery payload and handler maps."""
    if not isinstance(discovery, dict):
        return Topology(device_address=device_address)

    maps = maps or TopologyMaps()
    measurements_by_key = maps.measurements_by_key
    setpoints_by_key = maps.setpoints_by_key
    setpoint_constraints_by_key = maps.setpoint_constraints_by_key
    system_functions_by_key = maps.system_functions_by_key
    operation_modes_by_key = maps.operation_modes_by_key
    hvac_setpoint_relations_by_key = maps.hvac_setpoint_relations_by_key
    overruns_by_key = maps.overruns_by_key
    electrical_params_by_entity = maps.electrical_params_by_entity
    device_classification_by_entity = maps.device_classification_by_entity

    # Per-entity {setpointId: distinctive operation-mode type} from the HVAC
    # mode↔setpoint relations. A setpoint earns a label only when its entity
    # references ≥2 setpoints (otherwise there's nothing to disambiguate) and a
    # mode selects exactly that one setpoint — so `auto`, which spans several,
    # never labels, and a lone shared setpoint (e.g. DHW) stays unlabelled.
    distinctive_mode_by_entity = _distinctive_setpoint_modes(
        hvac_setpoint_relations_by_key, operation_modes_by_key
    )

    entities_raw = extract_entities(discovery)

    features_by_entity: Dict[Tuple[int, ...], List[Dict[str, Any]]] = {}
    feature_info = discovery.get("featureInformation")
    if isinstance(feature_info, list):
        for item in feature_info:
            if not isinstance(item, dict):
                continue
            desc = item.get("description")
            if not isinstance(desc, dict):
                continue
            faddr = desc.get("featureAddress")
            if not isinstance(faddr, dict):
                continue
            ent = entity_addr_list(faddr)
            feat = faddr.get("feature")
            ftype = desc.get("featureType")
            role = desc.get("role")
            if ent is None or not isinstance(feat, int) or not isinstance(ftype, str):
                continue
            features_by_entity.setdefault(tuple(ent), []).append(
                {
                    "feature": int(feat),
                    "feature_type": ftype,
                    "role": role if isinstance(role, str) else "",
                }
            )

    def _measurements_for(key: FeatureKey) -> Tuple[MeasurementDescription, ...]:
        entity, feature = key
        m = measurements_by_key.get(key, {})
        out: List[MeasurementDescription] = []
        for mid in sorted(m.keys()):
            d = m[mid] or {}
            out.append(
                MeasurementDescription(
                    measurement_id=mid,
                    scope_type=str_or_none(d.get("scopeType")),
                    measurement_type=str_or_none(d.get("measurementType")),
                    unit=unit_to_str(d.get("unit")),
                    key=measurement_key(
                        scope_type=d.get("scopeType"),
                        entity=entity,
                        feature=feature,
                        measurement_id=mid,
                    ),
                )
            )
        return tuple(out)

    def _setpoints_for(key: FeatureKey) -> Tuple[SetpointDescription, ...]:
        entity, feature = key
        m = setpoints_by_key.get(key, {})
        modes = distinctive_mode_by_entity.get(entity, {})
        constraints = setpoint_constraints_by_key.get(key, {})
        out: List[SetpointDescription] = []
        for sid in sorted(m.keys()):
            d = m[sid] or {}
            c = constraints.get(sid) or {}
            out.append(
                SetpointDescription(
                    setpoint_id=sid,
                    scope_type=str_or_none(d.get("scopeType")),
                    setpoint_type=str_or_none(d.get("setpointType")),
                    unit=unit_to_str(d.get("unit")),
                    description=_entity_description(d.get("description")),
                    key=setpoint_key(
                        scope_type=d.get("scopeType"),
                        setpoint_type=d.get("setpointType"),
                        entity=entity,
                        feature=feature,
                        setpoint_id=sid,
                    ),
                    mode_type=modes.get(sid),
                    range_min=_as_float(c.get("rangeMin")),
                    range_max=_as_float(c.get("rangeMax")),
                    step=_as_float(c.get("stepSize")),
                )
            )
        return tuple(out)

    def _system_functions_for(key: FeatureKey) -> Tuple[HvacSystemFunctionDescription, ...]:
        entity, feature = key
        m = system_functions_by_key.get(key, {})
        out: List[HvacSystemFunctionDescription] = []
        for sid in sorted(m.keys()):
            d = m[sid] or {}
            out.append(
                HvacSystemFunctionDescription(
                    system_function_id=sid,
                    system_function_type=str_or_none(d.get("systemFunctionType")),
                    key=hvac_mode_key(entity=entity, feature=feature, system_function_id=sid),
                )
            )
        return tuple(out)

    def _overruns_for(key: FeatureKey) -> Tuple[HvacOverrunDescription, ...]:
        entity, feature = key
        m = overruns_by_key.get(key, {})
        out: List[HvacOverrunDescription] = []
        for oid in sorted(m.keys()):
            d = m[oid] or {}
            affected = d.get("affectedSystemFunctionId") or []
            out.append(
                HvacOverrunDescription(
                    overrun_id=oid,
                    overrun_type=str_or_none(d.get("overrunType")),
                    affected_system_function_ids=tuple(int(x) for x in affected),
                    key=hvac_overrun_key(entity=entity, feature=feature, overrun_id=oid),
                )
            )
        return tuple(out)

    def _operation_modes_for(key: FeatureKey) -> Tuple[HvacOperationModeDescription, ...]:
        m = operation_modes_by_key.get(key, {})
        out: List[HvacOperationModeDescription] = []
        for oid in sorted(m.keys()):
            out.append(
                HvacOperationModeDescription(
                    operation_mode_id=oid,
                    operation_mode_type=str(m[oid]),
                )
            )
        return tuple(out)

    def _electrical_for(ent: Tuple[int, ...]) -> Tuple[ElectricalParameterDescription, ...]:
        m = electrical_params_by_entity.get(ent, {})
        out: List[ElectricalParameterDescription] = []
        for mid in sorted(m.keys()):
            d = m[mid] or {}
            pid = d.get("parameterId")
            out.append(
                ElectricalParameterDescription(
                    measurement_id=mid,
                    parameter_id=int(pid) if isinstance(pid, int) else None,
                    phases=str_or_none(d.get("phases")),
                    measurement_type=str_or_none(d.get("measurementType")),
                    variant=str_or_none(d.get("variant")),
                    voltage_type=str_or_none(d.get("voltageType")),
                    scope_type=str_or_none(d.get("scopeType")),
                )
            )
        return tuple(out)

    # First pass: build every entity's keyword bundle (everything but children),
    # keyed by address. ``order`` preserves the sorted address order from
    # extract_entities for the flat ``Topology.entities`` index.
    pending: Dict[Tuple[int, ...], Dict[str, Any]] = {}
    order: List[Tuple[int, ...]] = []
    for ent_raw in entities_raw:
        ent_tuple = tuple(ent_raw["entity"])
        feat_list = features_by_entity.get(ent_tuple, [])
        feat_list = sorted(feat_list, key=lambda f: (f["feature_type"], f["feature"]))
        feat_infos: List[FeatureInfo] = []
        for f in feat_list:
            key: FeatureKey = (ent_tuple, f["feature"])
            ft = f["feature_type"]
            feat_infos.append(
                FeatureInfo(
                    entity=ent_tuple,
                    feature=f["feature"],
                    feature_type=ft,
                    role=f["role"],
                    measurements=_measurements_for(key) if ft == "Measurement" else (),
                    setpoints=_setpoints_for(key) if ft == "Setpoint" else (),
                    system_functions=_system_functions_for(key) if ft == "HVAC" else (),
                    operation_modes=_operation_modes_for(key) if ft == "HVAC" else (),
                    overruns=_overruns_for(key) if ft == "HVAC" else (),
                    electrical_parameters=_electrical_for(ent_tuple)
                    if ft == "ElectricalConnection"
                    else (),
                )
            )
        classification = device_classification_by_entity.get(ent_tuple, {})
        manufacturer = classification.get("manufacturer") or {}
        user = classification.get("user") or {}
        pending[ent_tuple] = {
            "entity": ent_tuple,
            "entity_type": str_or_none(ent_raw.get("entityType")),
            "description": _entity_description(ent_raw.get("description")),
            "features": tuple(feat_infos),
            "user_label": str_or_none(user.get("userLabel")),
            "device_name": str_or_none(manufacturer.get("deviceName")),
            "device_code": str_or_none(manufacturer.get("deviceCode")),
            "serial_number": str_or_none(manufacturer.get("serialNumber")),
            "vendor_name": str_or_none(manufacturer.get("vendorName")),
            "brand_name": str_or_none(manufacturer.get("brandName")),
        }
        order.append(ent_tuple)

    # Second pass: resolve each entity's parent to its nearest *present* ancestor
    # (tolerating gaps where an intermediate entity wasn't reported), then build
    # the frozen EntityInfo objects deepest-first so a parent can embed the
    # already-built child objects.
    present = set(pending)

    def _nearest_parent(ent: Tuple[int, ...]) -> Optional[Tuple[int, ...]]:
        for k in range(len(ent) - 1, 0, -1):
            if ent[:k] in present:
                return ent[:k]
        return None

    children_of: Dict[Tuple[int, ...], List[Tuple[int, ...]]] = {e: [] for e in pending}
    for ent in pending:
        parent = _nearest_parent(ent)
        if parent is not None:
            children_of[parent].append(ent)

    built: Dict[Tuple[int, ...], EntityInfo] = {}
    for ent in sorted(pending, key=lambda e: (len(e), e), reverse=True):
        kids = tuple(built[c] for c in sorted(children_of[ent]))
        built[ent] = EntityInfo(**pending[ent], children=kids)

    return Topology(device_address=device_address, entities=tuple(built[e] for e in order))
