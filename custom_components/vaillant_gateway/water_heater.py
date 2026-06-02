"""Grouped DHW entity: combines temperature + setpoint + mode."""

from __future__ import annotations

import logging
from typing import List, Optional

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bounds import DHW_FALLBACK, setpoint_bounds
from .const import DOMAIN
from .coordinator import VaillantGatewayRuntime
from .entity import VaillantGatewayEntity
from .grouped_entity import GroupedHvacModeEntity
from .grouping import WaterHeaterGroup
from .vaillant_eebus import (
    EebusWriteError,
    MeasurementUpdate,
    SetpointUpdate,
    Update,
)

_LOGGER = logging.getLogger(__name__)

_PREFERRED_MODE_ORDER = ("off", "on", "auto")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: VaillantGatewayRuntime = hass.data[DOMAIN][entry.entry_id]
    snapshot = runtime.snapshot
    assert snapshot is not None

    entities = [
        VaillantGatewayWaterHeater(runtime, group)
        for group in snapshot.grouping.water_heater_entities
    ]
    async_add_entities(entities)


def _ordered_modes(raw: List[str]) -> List[str]:
    """Sort observed modes with the preferred order first, unknown ones appended."""
    out: List[str] = []
    seen = set()
    for m in _PREFERRED_MODE_ORDER:
        if m in raw and m not in seen:
            out.append(m)
            seen.add(m)
    for m in raw:
        if m not in seen:
            out.append(m)
            seen.add(m)
    return out


class VaillantGatewayWaterHeater(GroupedHvacModeEntity, VaillantGatewayEntity, WaterHeaterEntity):
    """DHW circuit as a single rich entity."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE | WaterHeaterEntityFeature.OPERATION_MODE
    )

    def __init__(self, runtime: VaillantGatewayRuntime, group: WaterHeaterGroup) -> None:
        super().__init__(
            runtime,
            f"water_heater_e{'_'.join(str(x) for x in group.entity)}",
            availability_keys=(group.temperature_key, group.setpoint_key, group.hvac_key),
        )
        self._group = group
        self._hvac_key = group.hvac_key
        self._attr_name = group.name or "Domestic Hot Water"

        # Honour the device's reported DHW setpoint limits; fall back to the
        # historical 30–70 °C / 0.5 °C guess when the gateway omits constraints.
        snapshot = runtime.snapshot
        desc = snapshot.topology.setpoint_description(group.setpoint_key) if snapshot else None
        bounds = setpoint_bounds(desc, DHW_FALLBACK)
        self._attr_min_temp = bounds.min
        self._attr_max_temp = bounds.max
        self._attr_target_temperature_step = bounds.step

        modes = [d.operation_mode_type for d in group.hvac_feature.operation_modes]
        self._attr_operation_list = _ordered_modes(modes)

    # -- state -------------------------------------------------------------

    @property
    def current_temperature(self) -> Optional[float]:
        u = self._runtime.latest.get(self._group.temperature_key)
        if isinstance(u, MeasurementUpdate) and isinstance(u.value, (int, float)):
            return float(u.value)
        return None

    @property
    def target_temperature(self) -> Optional[float]:
        u = self._runtime.latest.get(self._group.setpoint_key)
        if isinstance(u, SetpointUpdate) and isinstance(u.value, (int, float)):
            return float(u.value)
        return None

    @property
    def current_operation(self) -> Optional[str]:
        update = self._hvac_update()
        return update.mode if update is not None else None

    # -- writes ------------------------------------------------------------

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        try:
            await self._runtime.client.write_setpoint(self._group.setpoint_key, float(temperature))
        except EebusWriteError as err:
            raise HomeAssistantError(f"DHW setpoint write rejected: {err}") from err

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        self._ensure_mode_changeable()
        try:
            await self._runtime.client.write_hvac_mode(self._group.hvac_key, operation_mode)
        except EebusWriteError as err:
            raise HomeAssistantError(f"DHW mode write rejected: {err}") from err

    # -- updates -----------------------------------------------------------

    def _on_runtime_update(self, update: Optional[Update]) -> None:
        if update is None:
            self.async_write_ha_state()
            return
        if update.key in (
            self._group.temperature_key,
            self._group.setpoint_key,
            self._group.hvac_key,
        ):
            self.async_write_ha_state()
