"""Read-only sensors: measurements and HVAC mode text."""

from __future__ import annotations

from typing import List, Optional

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VaillantGatewayRuntime
from .entity import VaillantGatewayEntity
from .ha_meta import guess_ha_metadata
from .vaillant_eebus import HvacModeUpdate, MeasurementUpdate, Update
from .vaillant_eebus.naming import friendly_hvac_function_name, friendly_sensor_name, unit_to_ha


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: VaillantGatewayRuntime = hass.data[DOMAIN][entry.entry_id]
    snapshot = runtime.snapshot
    assert snapshot is not None, "snapshot must be populated before forwarding platforms"

    entities: List[VaillantGatewayEntity] = []
    for update in snapshot.update_by_key.values():
        if isinstance(update, MeasurementUpdate):
            entities.append(VaillantGatewayMeasurementSensor(runtime, update))
        elif isinstance(update, HvacModeUpdate):
            ent = snapshot.topology.entity(update.source_entity)
            entity_type = ent.entity_type if ent else None
            entities.append(VaillantGatewayHvacModeSensor(runtime, update, entity_type))

    async_add_entities(entities)


_STATE_CLASS = {
    "measurement": SensorStateClass.MEASUREMENT,
    "total_increasing": SensorStateClass.TOTAL_INCREASING,
}

_DEVICE_CLASS = {
    "temperature": SensorDeviceClass.TEMPERATURE,
    "frequency": SensorDeviceClass.FREQUENCY,
    "power": SensorDeviceClass.POWER,
    "energy": SensorDeviceClass.ENERGY,
    "current": SensorDeviceClass.CURRENT,
    "voltage": SensorDeviceClass.VOLTAGE,
}


class VaillantGatewayMeasurementSensor(VaillantGatewayEntity, SensorEntity):
    """One sensor per ``MeasurementUpdate`` key."""

    def __init__(self, runtime: VaillantGatewayRuntime, initial: MeasurementUpdate) -> None:
        super().__init__(runtime, initial.key, availability_keys=(initial.key,))
        self._key = initial.key
        self._refresh_metadata(initial)

    def _refresh_metadata(self, update: MeasurementUpdate) -> None:
        self._attr_name = friendly_sensor_name(
            update.scope_type,
            source_entity=update.source_entity,
            phase_info=update.phase,
        )
        meta = guess_ha_metadata(update.scope_type, update.unit)
        dc = meta.get("device_class") or ""
        sc = meta.get("state_class") or ""
        self._attr_device_class = _DEVICE_CLASS.get(dc)
        self._attr_state_class = _STATE_CLASS.get(sc)
        self._attr_native_unit_of_measurement = unit_to_ha(meta.get("unit") or update.unit) or None

    @property
    def native_value(self):
        update = self._runtime.latest.get(self._key)
        if isinstance(update, MeasurementUpdate):
            return update.value
        return None

    def _on_runtime_update(self, update: Optional[Update]) -> None:
        if update is None:
            self.async_write_ha_state()
            return
        if update.key != self._key:
            return
        if isinstance(update, MeasurementUpdate):
            self._refresh_metadata(update)
        self.async_write_ha_state()


class VaillantGatewayHvacModeSensor(VaillantGatewayEntity, SensorEntity):
    """One read-only text sensor per HVAC system function key."""

    def __init__(
        self,
        runtime: VaillantGatewayRuntime,
        initial: HvacModeUpdate,
        entity_type: Optional[str] = None,
    ) -> None:
        super().__init__(runtime, initial.key, availability_keys=(initial.key,))
        self._key = initial.key
        base = friendly_hvac_function_name(
            initial.system_function_type,
            system_function_id=initial.system_function_id,
            entity_type=entity_type,
        )
        self._attr_name = base
        self._attr_icon = "mdi:tune"

    @property
    def native_value(self):
        update = self._runtime.latest.get(self._key)
        if isinstance(update, HvacModeUpdate):
            return update.mode
        return None

    def _on_runtime_update(self, update: Optional[Update]) -> None:
        if update is None or update.key == self._key:
            self.async_write_ha_state()
