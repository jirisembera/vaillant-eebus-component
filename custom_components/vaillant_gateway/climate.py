"""Grouped heating zone entity: combines room temp + setpoint + heating mode.

SPINE operation modes for the observed device are ``off / on / auto / eco``.
HA's :class:`HVACMode` enum doesn't have an ``eco`` member, so ``eco`` is
surfaced as a preset on top of ``HVACMode.AUTO`` — this is the standard HA
pattern for vendor-specific schedule variants.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from homeassistant.components.climate import (
    PRESET_ECO,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bounds import ROOM_FALLBACK, setpoint_bounds
from .const import DOMAIN
from .coordinator import VaillantGatewayRuntime
from .entity import VaillantGatewayEntity
from .grouped_entity import GroupedHvacModeEntity
from .grouping import ClimateGroup
from .vaillant_eebus import (
    EebusWriteError,
    MeasurementUpdate,
    SetpointUpdate,
    Update,
)

_LOGGER = logging.getLogger(__name__)


# SPINE mode (lowercase) → HA hvac_mode (or None for "represent via preset")
_SPINE_TO_HVAC: Dict[str, HVACMode] = {
    "off": HVACMode.OFF,
    "on": HVACMode.HEAT,
    "auto": HVACMode.AUTO,
    "eco": HVACMode.AUTO,
}
# HA hvac_mode → SPINE mode to write
_HVAC_TO_SPINE: Dict[HVACMode, str] = {
    HVACMode.OFF: "off",
    HVACMode.HEAT: "on",
    HVACMode.AUTO: "auto",
}
# SPINE mode → preset (only when distinct from the plain AUTO mapping)
_SPINE_TO_PRESET: Dict[str, str] = {
    "eco": PRESET_ECO,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: VaillantGatewayRuntime = hass.data[DOMAIN][entry.entry_id]
    snapshot = runtime.snapshot
    assert snapshot is not None

    entities = [
        VaillantGatewayClimateZone(runtime, group) for group in snapshot.grouping.climate_entities
    ]
    async_add_entities(entities)


class VaillantGatewayClimateZone(GroupedHvacModeEntity, VaillantGatewayEntity, ClimateEntity):
    """Heating zone as a single rich entity."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, runtime: VaillantGatewayRuntime, group: ClimateGroup) -> None:
        super().__init__(
            runtime,
            f"climate_e{'_'.join(str(x) for x in group.entity)}",
            availability_keys=(group.temperature_key, group.setpoint_key, group.hvac_key),
        )
        self._group = group
        self._hvac_key = group.hvac_key
        self._attr_name = group.name or "Heating Zone"

        # Honour the device's reported room setpoint limits; fall back to
        # 5–30 °C / 0.5 °C guess when the gateway omits constraints.
        snapshot = runtime.snapshot
        desc = snapshot.topology.setpoint_description(group.setpoint_key) if snapshot else None
        bounds = setpoint_bounds(desc, ROOM_FALLBACK)
        self._attr_min_temp = bounds.min
        self._attr_max_temp = bounds.max
        self._attr_target_temperature_step = bounds.step

        raw_modes = {
            (d.operation_mode_type or "").lower() for d in group.hvac_feature.operation_modes
        }

        hvac_modes: List[HVACMode] = []
        for spine_mode in ("off", "on", "auto"):
            if spine_mode in raw_modes:
                mapped = _SPINE_TO_HVAC.get(spine_mode)
                if mapped is not None and mapped not in hvac_modes:
                    hvac_modes.append(mapped)
        unmapped = raw_modes - set(_SPINE_TO_HVAC.keys())
        if unmapped:
            _LOGGER.warning("Gateway climate: unmapped SPINE modes %s — ignored", sorted(unmapped))
        self._attr_hvac_modes = hvac_modes or [HVACMode.OFF]

        if "eco" in raw_modes:
            self._attr_preset_modes = [PRESET_NONE, PRESET_ECO]
            self._attr_supported_features = (
                ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
            )
        else:
            self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

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

    def _spine_mode(self) -> Optional[str]:
        update = self._hvac_update()
        if update is not None and isinstance(update.mode, str):
            return update.mode.lower()
        return None

    @property
    def hvac_mode(self) -> Optional[HVACMode]:
        mode = self._spine_mode()
        if mode is None:
            return None
        return _SPINE_TO_HVAC.get(mode)

    @property
    def preset_mode(self) -> Optional[str]:
        if not getattr(self, "_attr_preset_modes", None):
            return None
        mode = self._spine_mode()
        if mode is None:
            return None
        return _SPINE_TO_PRESET.get(mode, PRESET_NONE)

    # -- writes ------------------------------------------------------------

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        try:
            await self._runtime.client.write_setpoint(self._group.setpoint_key, float(temperature))
        except EebusWriteError as err:
            raise HomeAssistantError(f"Setpoint write rejected: {err}") from err

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        spine = _HVAC_TO_SPINE.get(hvac_mode)
        if spine is None:
            raise HomeAssistantError(f"HVAC mode {hvac_mode} not supported on this device")
        self._ensure_mode_changeable()
        try:
            await self._runtime.client.write_hvac_mode(self._group.hvac_key, spine)
        except EebusWriteError as err:
            raise HomeAssistantError(f"HVAC mode write rejected: {err}") from err

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == PRESET_ECO:
            spine = "eco"
        elif preset_mode == PRESET_NONE:
            spine = "auto"
        else:
            raise HomeAssistantError(f"Unknown preset {preset_mode}")
        self._ensure_mode_changeable()
        try:
            await self._runtime.client.write_hvac_mode(self._group.hvac_key, spine)
        except EebusWriteError as err:
            raise HomeAssistantError(f"Preset write rejected: {err}") from err

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
