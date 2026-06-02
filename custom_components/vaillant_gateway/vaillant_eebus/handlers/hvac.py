"""Handler for the SPINE HVAC feature."""

from __future__ import annotations

import logging
import time
from types import MappingProxyType
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Tuple

from ..events import HvacModeUpdate, HvacOverrunUpdate
from ..keys import hvac_mode_key, hvac_overrun_key
from ..parsers import (
    ParsedOverrun,
    ParsedSystemFunction,
    parse_hvac_operation_mode_descriptions,
    parse_hvac_overrun_descriptions,
    parse_hvac_overrun_list,
    parse_hvac_system_function_descriptions,
    parse_hvac_system_function_list,
    parse_hvac_system_function_setpoint_relations,
)
from ..spine import WriteFrame
from .base import FeatureHandler, FeatureKey

logger = logging.getLogger(__name__)


class HVACHandler(FeatureHandler):
    feature_type: ClassVar[str] = "HVAC"
    handled_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "hvacOperationModeDescriptionListData",
            "hvacSystemFunctionDescriptionListData",
            "hvacSystemFunctionSetpointRelationListData",
            "hvacSystemFunctionListData",
            "hvacOverrunDescriptionListData",
            "hvacOverrunListData",
        }
    )
    description_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "hvacOperationModeDescriptionListData",
            "hvacSystemFunctionDescriptionListData",
        }
    )
    # Best-effort reads that ride along with the descriptions but don't gate
    # bring-up: the static mode↔setpoint relations, plus the overrun metadata +
    # state (only the DHW feature has overruns, so they can't gate every server).
    auxiliary_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "hvacSystemFunctionSetpointRelationListData",
            "hvacOverrunDescriptionListData",
            "hvacOverrunListData",
        }
    )
    value_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "hvacSystemFunctionListData",
        }
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._op_mode_maps: Dict[FeatureKey, Dict[int, str]] = {}
        self._sys_func_maps: Dict[FeatureKey, Dict[int, Dict[str, Any]]] = {}
        # {(entity, feature): {systemFunctionId: {operationModeId: [setpointId, ...]}}}
        self._setpoint_relation_maps: Dict[FeatureKey, Dict[int, Dict[int, List[int]]]] = {}
        # {(entity, feature): {overrunId: {overrunType, affectedSystemFunctionId}}}
        self._overrun_desc_maps: Dict[FeatureKey, Dict[int, Dict[str, Any]]] = {}

    @property
    def operation_mode_map(self) -> Mapping[FeatureKey, Dict[int, str]]:
        """Read-only {(entity, feature): {operationModeId: operationModeType}}."""
        return MappingProxyType(self._op_mode_maps)

    @property
    def system_function_map(self) -> Mapping[FeatureKey, Dict[int, Dict[str, Any]]]:
        """Read-only {(entity, feature): {systemFunctionId: {systemFunctionType, ...}}}."""
        return MappingProxyType(self._sys_func_maps)

    @property
    def setpoint_relation_map(self) -> Mapping[FeatureKey, Dict[int, Dict[int, List[int]]]]:
        """Read-only {(entity, feature): {systemFunctionId: {operationModeId: [setpointId, ...]}}}."""
        return MappingProxyType(self._setpoint_relation_maps)

    @property
    def overrun_description_map(self) -> Mapping[FeatureKey, Dict[int, Dict[str, Any]]]:
        """Read-only {(entity, feature): {overrunId: {overrunType, affectedSystemFunctionId}}}."""
        return MappingProxyType(self._overrun_desc_maps)

    def _handle(self, cmd_key: str, hdr: Dict[str, Any], cmd: Dict[str, Any]) -> None:
        src_addr, key = self._source_key(hdr)
        if cmd_key == "hvacOperationModeDescriptionListData":
            mode_map = parse_hvac_operation_mode_descriptions(cmd)
            if key is not None and mode_map:
                self._op_mode_maps[key] = mode_map
            return
        if cmd_key == "hvacSystemFunctionDescriptionListData":
            sf_map = parse_hvac_system_function_descriptions(cmd)
            if key is not None and sf_map:
                self._sys_func_maps[key] = sf_map
            return
        if cmd_key == "hvacSystemFunctionSetpointRelationListData":
            rel_map = parse_hvac_system_function_setpoint_relations(cmd)
            if key is not None and rel_map:
                self._setpoint_relation_maps[key] = rel_map
            return
        if cmd_key == "hvacOverrunDescriptionListData":
            ov_desc = parse_hvac_overrun_descriptions(cmd)
            if key is not None and ov_desc:
                self._overrun_desc_maps[key] = ov_desc
            return
        if cmd_key == "hvacOverrunListData":
            ov_desc = self._overrun_desc_maps.get(key, {}) if key is not None else {}
            for entry in parse_hvac_overrun_list(cmd, source_address=src_addr):
                self._emit_overrun(entry, ov_desc=ov_desc)
            return
        if cmd_key == "hvacSystemFunctionListData":
            mode_map = self._op_mode_maps.get(key, {}) if key is not None else {}
            sf_map = self._sys_func_maps.get(key, {}) if key is not None else {}
            for entry in parse_hvac_system_function_list(cmd, source_address=src_addr):
                self._emit_hvac_mode(entry, mode_map=mode_map, sf_map=sf_map)

    def _emit_hvac_mode(
        self,
        entry: ParsedSystemFunction,
        *,
        mode_map: Dict[int, str],
        sf_map: Dict[int, Dict[str, Any]],
    ) -> None:
        sid = entry.system_function_id
        cur = entry.current_operation_mode_id
        mode = mode_map.get(cur) if isinstance(cur, int) else None
        sf_meta = sf_map.get(sid) if isinstance(sf_map, dict) else None
        sf_type = sf_meta.get("systemFunctionType") if isinstance(sf_meta, dict) else None
        ent_tuple = entry.source.entity
        feat_int = entry.source.feature_or_zero

        self._on_update(
            HvacModeUpdate(
                key=hvac_mode_key(entity=ent_tuple, feature=feat_int, system_function_id=sid),
                value=mode,
                unit="",
                timestamp=time.time(),
                source_entity=ent_tuple,
                source_feature=feat_int,
                system_function_id=sid,
                system_function_type=str(sf_type or ""),
                mode=mode,
                mode_changeable=entry.is_operation_mode_changeable,
                overrun_active=entry.is_overrun_active,
            )
        )

    def _emit_overrun(self, ov: ParsedOverrun, *, ov_desc: Dict[int, Dict[str, Any]]) -> None:
        ent_tuple = ov.source.entity
        feat_int = ov.source.feature_or_zero
        meta = ov_desc.get(ov.overrun_id, {})
        affected = meta.get("affectedSystemFunctionId") or []
        status = (ov.status or "").lower()

        self._on_update(
            HvacOverrunUpdate(
                key=hvac_overrun_key(entity=ent_tuple, feature=feat_int, overrun_id=ov.overrun_id),
                value=ov.status,
                unit="",
                timestamp=time.time(),
                source_entity=ent_tuple,
                source_feature=feat_int,
                overrun_id=ov.overrun_id,
                active=status == "active",
                overrun_type=str(meta.get("overrunType") or ""),
                affected_system_function_ids=tuple(int(x) for x in affected),
            )
        )

    def resolve_operation_mode(self, entity_tuple: Tuple[int, ...], feature: int, mode: Any) -> int:
        """Map a mode (int id or string name) to its ``operationModeId``."""
        key: FeatureKey = (tuple(int(x) for x in entity_tuple), int(feature))
        mode_map = self._op_mode_maps.get(key, {})
        if not mode_map:
            raise ValueError(
                f"No HVAC operation modes known for entity={list(entity_tuple)} feature={feature}"
            )
        if isinstance(mode, bool):
            raise ValueError(f"HVAC mode must be int or str, not bool ({mode!r})")
        if isinstance(mode, int):
            if mode not in mode_map:
                raise ValueError(
                    f"Unknown operationModeId={mode} on entity={list(entity_tuple)} feature={feature}; "
                    f"known ids: {sorted(mode_map.keys())}"
                )
            return int(mode)
        if isinstance(mode, str):
            wanted = mode.strip().lower()
            for mid, mtype in mode_map.items():
                if isinstance(mtype, str) and mtype.lower() == wanted:
                    return int(mid)
            raise ValueError(
                f"Unknown operation mode {mode!r} on entity={list(entity_tuple)} feature={feature}; "
                f"known modes: {sorted(set(mode_map.values()))}"
            )
        raise ValueError(f"HVAC mode must be int or str, got {type(mode).__name__}")

    def build_write(
        self,
        *,
        entity_tuple: Tuple[int, ...],
        feature: int,
        system_function_id: int,
        operation_mode_id: int,
    ) -> WriteFrame:
        """Validate and build an HVAC operation-mode :class:`WriteFrame`.

        Returns a frame for :meth:`_SpineSession.write` to send — pure, touches
        no transport. The destination carries only the target entity + feature;
        the session fills in the connected device. Raises :class:`ValueError` if
        the ids or target server can't be resolved.
        """
        key: FeatureKey = (tuple(int(x) for x in entity_tuple), int(feature))
        sf_map = self._sys_func_maps.get(key, {})
        if sf_map and system_function_id not in sf_map:
            raise ValueError(
                f"Unknown systemFunctionId={system_function_id} on "
                f"entity={list(entity_tuple)} feature={feature}; "
                f"known ids: {sorted(sf_map.keys())}"
            )
        mode_map = self._op_mode_maps.get(key, {})
        if mode_map and operation_mode_id not in mode_map:
            raise ValueError(
                f"Unknown operationModeId={operation_mode_id} on "
                f"entity={list(entity_tuple)} feature={feature}; "
                f"known ids: {sorted(mode_map.keys())}"
            )
        server = self.find_server(entity_tuple, feature)
        if server is None:
            raise ValueError(
                f"No HVAC server matches entity={list(entity_tuple)} feature={feature}"
            )
        cmd = {
            "hvacSystemFunctionListData": {
                "hvacSystemFunctionData": [
                    {
                        "systemFunctionId": int(system_function_id),
                        "currentOperationModeId": int(operation_mode_id),
                    }
                ]
            }
        }
        return WriteFrame(
            address_source=self._local_client_feature,
            address_destination=server,
            cmd=cmd,
        )

    def build_overrun_write(
        self,
        *,
        entity_tuple: Tuple[int, ...],
        feature: int,
        overrun_id: int,
        active: bool,
    ) -> WriteFrame:
        """Validate and build an HVAC overrun (e.g. DHW boost) :class:`WriteFrame`.

        Sets ``overrunStatus`` to ``active`` / ``inactive``. Pure — returns a
        frame for :meth:`_SpineSession.write` to send. Raises :class:`ValueError`
        if the overrun id or target server can't be resolved.
        """
        key: FeatureKey = (tuple(int(x) for x in entity_tuple), int(feature))
        ov_desc = self._overrun_desc_maps.get(key, {})
        if ov_desc and overrun_id not in ov_desc:
            raise ValueError(
                f"Unknown overrunId={overrun_id} on "
                f"entity={list(entity_tuple)} feature={feature}; "
                f"known ids: {sorted(ov_desc.keys())}"
            )
        server = self.find_server(entity_tuple, feature)
        if server is None:
            raise ValueError(
                f"No HVAC server matches entity={list(entity_tuple)} feature={feature}"
            )
        cmd = {
            "hvacOverrunListData": {
                "hvacOverrunData": [
                    {
                        "overrunId": int(overrun_id),
                        "overrunStatus": "active" if active else "inactive",
                    }
                ]
            }
        }
        return WriteFrame(
            address_source=self._local_client_feature,
            address_destination=server,
            cmd=cmd,
        )
