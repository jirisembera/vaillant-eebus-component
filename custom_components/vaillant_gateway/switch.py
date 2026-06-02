"""Writable switch entities for HVAC overruns (e.g. the one-time DHW boost)."""

from __future__ import annotations

import logging
from typing import List, Optional

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VaillantGatewayRuntime
from .entity import VaillantGatewayEntity
from .vaillant_eebus import EebusWriteError, HvacOverrunUpdate, Update
from .vaillant_eebus.naming import friendly_overrun_name

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
    for key, update in snapshot.update_by_key.items():
        if not isinstance(update, HvacOverrunUpdate):
            continue
        # The overrun type (for naming) is static — read it from the topology,
        # which is fully populated by the time the snapshot is built.
        desc = snapshot.topology.overrun_description(key)
        overrun_type = desc.overrun_type if desc else update.overrun_type
        ent = snapshot.topology.entity(update.source_entity)
        entity_type = ent.entity_type if ent else None
        entities.append(VaillantGatewayOverrunSwitch(runtime, update, overrun_type, entity_type))

    async_add_entities(entities)


class VaillantGatewayOverrunSwitch(VaillantGatewayEntity, SwitchEntity):
    """Writable switch for one HVAC overrun (e.g. one-time DHW boost)."""

    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        runtime: VaillantGatewayRuntime,
        initial: HvacOverrunUpdate,
        overrun_type: Optional[str] = None,
        entity_type: Optional[str] = None,
    ) -> None:
        super().__init__(runtime, initial.key, availability_keys=(initial.key,))
        self._key = initial.key
        self._attr_name = friendly_overrun_name(overrun_type, entity_type=entity_type)
        self._attr_icon = "mdi:water-boiler"

    @property
    def is_on(self) -> Optional[bool]:
        update = self._runtime.latest.get(self._key)
        if isinstance(update, HvacOverrunUpdate):
            return update.active
        return None

    async def async_turn_on(self, **kwargs) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write(False)

    async def _write(self, active: bool) -> None:
        try:
            await self._runtime.client.write_overrun(self._key, active)
        except EebusWriteError as err:
            raise HomeAssistantError(f"DHW boost write rejected: {err}") from err

    def _on_runtime_update(self, update: Optional[Update]) -> None:
        if update is None or update.key == self._key:
            self.async_write_ha_state()
