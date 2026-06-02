"""Handler for the SPINE Measurement feature."""

from __future__ import annotations

import logging
import time
from types import MappingProxyType
from typing import Any, ClassVar, Dict, FrozenSet, Mapping

from ..events import MeasurementUpdate
from ..keys import measurement_key
from ..naming import unit_to_ha
from ..parsers import ParsedMeasurement, parse_measurement_description, parse_measurement_list
from ..spine import SpineAddress
from .base import FeatureHandler, FeatureKey, OnUpdate
from .electrical import ElectricalHandler

logger = logging.getLogger(__name__)


class MeasurementHandler(FeatureHandler):
    feature_type: ClassVar[str] = "Measurement"
    handled_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "measurementDescriptionListData",
            "measurementListData",
        }
    )
    description_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "measurementDescriptionListData",
        }
    )
    value_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "measurementListData",
        }
    )

    def __init__(
        self,
        *,
        local_client_feature: SpineAddress,
        on_update: OnUpdate,
        electrical: ElectricalHandler,
    ) -> None:
        super().__init__(local_client_feature=local_client_feature, on_update=on_update)
        self._electrical = electrical
        # Measurement IDs are server-local; key desc maps by source feature address.
        self._desc_maps: Dict[FeatureKey, Dict[int, Dict[str, Any]]] = {}

    @property
    def description_map(self) -> Mapping[FeatureKey, Dict[int, Dict[str, Any]]]:
        """Read-only {(entity, feature): {measurementId: {scopeType, unit, measurementType}}}."""
        return MappingProxyType(self._desc_maps)

    def _handle(self, cmd_key: str, hdr: Dict[str, Any], cmd: Dict[str, Any]) -> None:
        src_addr, key = self._source_key(hdr)
        if cmd_key == "measurementDescriptionListData":
            desc_map = parse_measurement_description(cmd)
            if key is not None and desc_map:
                self._desc_maps[key] = desc_map
            logger.debug("✅ [MEASUREMENT] descriptions: %d entries", len(desc_map))
            return
        if cmd_key == "measurementListData":
            desc_map = self._desc_maps.get(key, {}) if key is not None else {}
            for parsed in parse_measurement_list(cmd, desc_map, source_address=src_addr):
                self._emit_measurement(parsed)

    def _emit_measurement(self, m: ParsedMeasurement) -> None:
        ent_tuple = m.source.entity
        feat_int = m.source.feature_or_zero
        phase_info = self._electrical.phase_for(ent_tuple, m.measurement_id)

        self._on_update(
            MeasurementUpdate(
                key=measurement_key(
                    scope_type=m.scope_type,
                    entity=ent_tuple,
                    feature=feat_int,
                    measurement_id=m.measurement_id,
                ),
                value=m.value,
                unit=unit_to_ha(m.unit),
                timestamp=time.time(),
                source_entity=ent_tuple,
                source_feature=feat_int,
                scope_type=m.scope_type,
                measurement_type=m.measurement_type,
                measurement_id=m.measurement_id,
                phase=phase_info,
            )
        )
