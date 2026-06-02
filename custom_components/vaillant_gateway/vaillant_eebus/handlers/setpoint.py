"""Handler for the SPINE Setpoint feature."""

from __future__ import annotations

import logging
import time
from types import MappingProxyType
from typing import Any, ClassVar, Dict, FrozenSet, Mapping, Tuple

from ..events import SetpointUpdate
from ..keys import setpoint_key
from ..naming import unit_to_ha
from ..parsers import (
    ParsedSetpoint,
    float_to_scaled_number,
    parse_setpoint_constraints,
    parse_setpoint_descriptions,
    parse_setpoint_list,
    setpoint_scope,
)
from ..spine import WriteFrame
from .base import FeatureHandler, FeatureKey

logger = logging.getLogger(__name__)


class SetpointHandler(FeatureHandler):
    feature_type: ClassVar[str] = "Setpoint"
    handled_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "setpointDescriptionListData",
            "setpointConstraintsListData",
            "setpointListData",
        }
    )
    description_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "setpointDescriptionListData",
        }
    )
    # Static per-setpoint min/max/step; best-effort (not gating bring-up). A
    # gateway that never answers just leaves the entity on its default bounds.
    auxiliary_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "setpointConstraintsListData",
        }
    )
    value_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "setpointListData",
        }
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._desc_maps: Dict[FeatureKey, Dict[int, Dict[str, Any]]] = {}
        self._constraint_maps: Dict[FeatureKey, Dict[int, Dict[str, Any]]] = {}

    @property
    def description_map(self) -> Mapping[FeatureKey, Dict[int, Dict[str, Any]]]:
        """Read-only {(entity, feature): {setpointId: {setpointType, scopeType, unit, description}}}."""
        return MappingProxyType(self._desc_maps)

    @property
    def constraint_map(self) -> Mapping[FeatureKey, Dict[int, Dict[str, Any]]]:
        """Read-only {(entity, feature): {setpointId: {rangeMin, rangeMax, stepSize}}}."""
        return MappingProxyType(self._constraint_maps)

    def _handle(self, cmd_key: str, hdr: Dict[str, Any], cmd: Dict[str, Any]) -> None:
        src_addr, key = self._source_key(hdr)
        if cmd_key == "setpointDescriptionListData":
            sp_desc = parse_setpoint_descriptions(cmd)
            if key is not None and sp_desc:
                self._desc_maps[key] = sp_desc
            return
        if cmd_key == "setpointConstraintsListData":
            sp_constraints = parse_setpoint_constraints(cmd)
            if key is not None and sp_constraints:
                self._constraint_maps[key] = sp_constraints
            return
        if cmd_key == "setpointListData":
            sp_desc = self._desc_maps.get(key, {}) if key is not None else {}
            for parsed in parse_setpoint_list(cmd, sp_desc, source_address=src_addr):
                self._emit_setpoint(parsed)

    def _emit_setpoint(self, sp: ParsedSetpoint) -> None:
        ent_tuple = sp.source.entity
        feat_int = sp.source.feature_or_zero
        scope = setpoint_scope(sp.scope_type, sp.setpoint_type)

        self._on_update(
            SetpointUpdate(
                key=setpoint_key(
                    scope_type=sp.scope_type,
                    setpoint_type=sp.setpoint_type,
                    entity=ent_tuple,
                    feature=feat_int,
                    setpoint_id=sp.setpoint_id,
                ),
                value=sp.value,
                unit=unit_to_ha(sp.unit or "degC"),
                timestamp=time.time(),
                source_entity=ent_tuple,
                source_feature=feat_int,
                scope_type=scope,
                setpoint_type=str(sp.setpoint_type or ""),
                setpoint_id=sp.setpoint_id,
            )
        )

    def build_write(
        self,
        *,
        entity_tuple: Tuple[int, ...],
        feature: int,
        setpoint_id: int,
        value: float,
        scale: int = -1,
    ) -> WriteFrame:
        """Validate and build a setpoint :class:`WriteFrame`.

        Returns a frame for :meth:`_SpineSession.write` to send — pure, touches
        no transport. The destination carries only the target entity + feature;
        the session fills in the connected device. Raises :class:`ValueError` if
        the setpoint id or target server can't be resolved against the cached
        descriptions.
        """
        key: FeatureKey = (tuple(int(x) for x in entity_tuple), int(feature))
        desc = self._desc_maps.get(key)
        if not desc:
            raise ValueError(
                f"No setpoint descriptions known for entity={list(entity_tuple)} feature={feature}"
            )
        if setpoint_id not in desc:
            raise ValueError(
                f"Unknown setpointId={setpoint_id} on entity={list(entity_tuple)} feature={feature}; "
                f"known ids: {sorted(desc.keys())}"
            )
        server = self.find_server(entity_tuple, feature)
        if server is None:
            raise ValueError(
                f"No Setpoint server matches entity={list(entity_tuple)} feature={feature}"
            )
        cmd = {
            "setpointListData": {
                "setpointData": [
                    {
                        "setpointId": int(setpoint_id),
                        "value": float_to_scaled_number(float(value), scale=scale),
                    }
                ]
            }
        }
        return WriteFrame(
            address_source=self._local_client_feature,
            address_destination=server,
            cmd=cmd,
        )
