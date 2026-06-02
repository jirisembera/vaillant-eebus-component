"""Shared base class for Vaillant EEBUS entities."""

from __future__ import annotations

from typing import Optional, Sequence

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, UNKNOWN_MODEL
from .coordinator import VaillantGatewayRuntime
from .vaillant_eebus import Update


class VaillantGatewayEntity(Entity):
    """Base class wiring availability, device_info and listener registration."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        runtime: VaillantGatewayRuntime,
        unique_suffix: str,
        *,
        availability_keys: Sequence[str] = (),
    ) -> None:
        self._runtime = runtime
        # Keys this entity needs to be considered available. Empty = follow the
        # device. With keys, the entity is available only while at least one of
        # them reported on the current connection (see runtime.is_live), so a
        # feature lost on reconnect shows N/A while the rest of the device stays.
        self._availability_keys = tuple(availability_keys)
        self._attr_unique_id = f"{runtime.device_ski}_{unique_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.device_ski)},
            name=runtime.device_name,
            manufacturer=runtime.device_brand,
            model=runtime.device_model or UNKNOWN_MODEL,
            model_id=runtime.device_model_id or None,
            serial_number=runtime.device_serial or None,
        )
        self._unsub_listener = None

    @property
    def available(self) -> bool:
        if not self._runtime.available:
            return False
        if not self._availability_keys:
            return True
        return any(self._runtime.is_live(k) for k in self._availability_keys)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_listener = self._runtime.async_add_listener(self._on_runtime_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_listener is not None:
            self._unsub_listener()
            self._unsub_listener = None
        await super().async_will_remove_from_hass()

    def _on_runtime_update(self, update: Optional[Update]) -> None:
        """Override to filter updates by key. Default: write state on every event."""
        self.async_write_ha_state()
