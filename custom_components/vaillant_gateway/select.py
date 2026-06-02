"""Writable select entities for HVAC operation modes."""

from __future__ import annotations

import logging
from typing import List, Optional

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VaillantGatewayRuntime
from .entity import VaillantGatewayEntity
from .vaillant_eebus import EebusWriteError, FeatureInfo, HvacModeUpdate, Update
from .vaillant_eebus.naming import friendly_hvac_function_name

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: VaillantGatewayRuntime = hass.data[DOMAIN][entry.entry_id]
    snapshot = runtime.snapshot
    assert snapshot is not None

    entities: List[VaillantGatewayEntity] = []
    grouped = snapshot.grouping.grouped_hvac_keys
    for key, update in snapshot.update_by_key.items():
        if not isinstance(update, HvacModeUpdate):
            continue
        if key in grouped:
            continue
        feature = snapshot.topology.feature(update.source_entity, update.source_feature)
        if feature is None or not feature.operation_modes:
            continue
        ent = snapshot.topology.entity(update.source_entity)
        entity_type = ent.entity_type if ent else None
        entities.append(VaillantGatewayHvacModeSelect(runtime, update, feature, entity_type))

    async_add_entities(entities)


class VaillantGatewayHvacModeSelect(VaillantGatewayEntity, SelectEntity):
    """Writable select for one HVAC system function."""

    def __init__(
        self,
        runtime: VaillantGatewayRuntime,
        initial: HvacModeUpdate,
        feature: FeatureInfo,
        entity_type: Optional[str] = None,
    ) -> None:
        super().__init__(runtime, initial.key, availability_keys=(initial.key,))
        self._key = initial.key
        self._attr_name = friendly_hvac_function_name(
            initial.system_function_type,
            system_function_id=initial.system_function_id,
            entity_type=entity_type,
        )
        self._attr_options = [d.operation_mode_type for d in feature.operation_modes]
        self._attr_icon = "mdi:tune-vertical"

    @property
    def current_option(self) -> Optional[str]:
        update = self._runtime.latest.get(self._key)
        if isinstance(update, HvacModeUpdate):
            return update.mode
        return None

    async def async_select_option(self, option: str) -> None:
        try:
            await self._runtime.client.write_hvac_mode(self._key, option)
        except EebusWriteError as err:
            raise HomeAssistantError(f"HVAC mode write rejected: {err}") from err

    def _on_runtime_update(self, update: Optional[Update]) -> None:
        if update is None or update.key == self._key:
            self.async_write_ha_state()
