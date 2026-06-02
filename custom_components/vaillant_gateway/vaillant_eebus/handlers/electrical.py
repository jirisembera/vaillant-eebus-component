"""Handler for the SPINE ElectricalConnection feature.

ElectricalConnection is read-only (no subscription, no value reads) — the
parameter descriptions are static metadata that links Measurement IDs to
per-phase info. :class:`MeasurementHandler` reads :meth:`phase_for` while
emitting its updates; the linear bring-up reads every handler's descriptions
before any values, so the phase metadata is already present on the first
measurement emit.
"""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Any, ClassVar, Dict, FrozenSet, Mapping, Optional, Tuple

from ..parsers import parse_electrical_param_descriptions
from ..spine import SpineAddress
from .base import FeatureHandler

logger = logging.getLogger(__name__)


class ElectricalHandler(FeatureHandler):
    feature_type: ClassVar[str] = "ElectricalConnection"
    handled_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "electricalConnectionParameterDescriptionListData",
            "electricalConnectionDescriptionListData",
        }
    )
    description_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "electricalConnectionParameterDescriptionListData",
            "electricalConnectionDescriptionListData",
        }
    )
    value_cmd_keys: ClassVar[FrozenSet[str]] = frozenset()
    subscribe_on_kickoff: ClassVar[bool] = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Keyed by entity prefix tuple — the ElectricalConnection feature shares
        # an entity with the Measurement feature it describes.
        self._param_maps: Dict[Tuple[int, ...], Dict[int, Dict[str, Any]]] = {}

    @property
    def parameter_map(self) -> Mapping[Tuple[int, ...], Dict[int, Dict[str, Any]]]:
        """Read-only {entity: {measurementId: {phases, measurementType, variant, ...}}}."""
        return MappingProxyType(self._param_maps)

    def _handle(self, cmd_key: str, hdr: Dict[str, Any], cmd: Dict[str, Any]) -> None:
        if cmd_key == "electricalConnectionParameterDescriptionListData":
            param_map = parse_electrical_param_descriptions(cmd)
            addr = SpineAddress.from_raw(hdr.get("addressSource"))
            if addr is not None:
                self._param_maps[addr.entity] = param_map
            logger.debug("✅ [ELECTRICAL] parameter descriptions: %d entries", len(param_map))
            return
        if cmd_key == "electricalConnectionDescriptionListData":
            logger.debug("✅ [ELECTRICAL] connection descriptions received (ignored)")

    def phase_for(
        self,
        entity: Tuple[int, ...],
        measurement_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Phase metadata (if any) for the given Measurement ID on `entity`."""
        if not entity:
            return None
        return self._param_maps.get(entity, {}).get(measurement_id)
