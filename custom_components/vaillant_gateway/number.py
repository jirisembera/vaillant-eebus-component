"""Writable number entities for SPINE setpoints."""

from __future__ import annotations

import logging
from typing import List, Optional

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bounds import DHW_FALLBACK, ROOM_FALLBACK, Bounds, setpoint_bounds
from .const import DOMAIN
from .coordinator import VaillantGatewayRuntime
from .entity import VaillantGatewayEntity
from .vaillant_eebus import EebusWriteError, SetpointDescription, SetpointUpdate, Update
from .vaillant_eebus.naming import friendly_setpoint_name, unit_to_ha

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
    grouped = snapshot.grouping.grouped_setpoint_keys
    for key, update in snapshot.update_by_key.items():
        if not isinstance(update, SetpointUpdate):
            continue
        if key in grouped:
            continue
        ent = snapshot.topology.entity(update.source_entity)
        entity_type = ent.entity_type if ent else None
        # The owner's zone label (e.g. "PODLAHOVKA") names the setpoint when the
        # device provides one; the distinctive HVAC mode (e.g. "eco") qualifies it.
        user_label = snapshot.topology.nearest_user_label(update.source_entity)
        # The matching description carries both the distinctive HVAC mode and the
        # device-reported writable bounds (min/max/step) for this setpoint.
        desc = snapshot.topology.setpoint_description(key)
        mode_type = desc.mode_type if desc else None
        entities.append(
            VaillantGatewaySetpointNumber(
                runtime,
                update,
                entity_type,
                user_label=user_label,
                mode_type=mode_type,
                desc=desc,
            )
        )

    async_add_entities(entities)


def _fallback_bounds(scope_type: Optional[str], entity_type: Optional[str]) -> Bounds:
    """Pick the fallback range from the device-provided scope / entity type."""
    s = (scope_type or "").lower()
    et = (entity_type or "").lower()
    if "dhw" in s or "dhw" in et:
        return DHW_FALLBACK
    if "room" in s or et in ("hvacroom", "heatingzone", "heatingcircuit", "coolingcircuit"):
        return ROOM_FALLBACK
    _LOGGER.warning(
        "no setpoint bounds known for scope=%r entity_type=%r; defaulting to 0..100",
        scope_type,
        entity_type,
    )
    return Bounds(0.0, 100.0, 0.5)


class VaillantGatewaySetpointNumber(VaillantGatewayEntity, NumberEntity):
    """Writable number for one SPINE setpoint."""

    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        runtime: VaillantGatewayRuntime,
        initial: SetpointUpdate,
        entity_type: Optional[str] = None,
        *,
        user_label: Optional[str] = None,
        mode_type: Optional[str] = None,
        desc: Optional[SetpointDescription] = None,
    ) -> None:
        super().__init__(runtime, initial.key, availability_keys=(initial.key,))
        self._key = initial.key
        self._attr_name = friendly_setpoint_name(
            initial.scope_type,
            entity_type=entity_type,
            mode_type=mode_type,
            user_label=user_label,
        )

        unit = unit_to_ha(initial.unit) or "°C"
        self._attr_native_unit_of_measurement = unit
        if unit in ("°C", "°F"):
            self._attr_device_class = NumberDeviceClass.TEMPERATURE

        bounds = setpoint_bounds(desc, _fallback_bounds(initial.scope_type, entity_type))
        self._attr_native_min_value = bounds.min
        self._attr_native_max_value = bounds.max
        self._attr_native_step = bounds.step

    @property
    def native_value(self):
        update = self._runtime.latest.get(self._key)
        if isinstance(update, SetpointUpdate):
            return update.value
        return None

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self._runtime.client.write_setpoint(self._key, value)
        except EebusWriteError as err:
            raise HomeAssistantError(f"Setpoint write rejected: {err}") from err

    def _on_runtime_update(self, update: Optional[Update]) -> None:
        if update is None or update.key == self._key:
            self.async_write_ha_state()
