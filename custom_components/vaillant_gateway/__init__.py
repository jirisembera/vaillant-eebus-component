"""Vaillant EEBUS native Home Assistant integration.

Wraps the vendored :mod:`.vaillant_eebus` SHIP/SPINE client as HA entities. The MQTT
bridge under ``tools/eebus_to_mqtt/`` in the repo is unrelated and left untouched.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import VaillantGatewayRuntime

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.WATER_HEATER,
    Platform.CLIMATE,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a configured EEBUS gateway."""
    runtime = VaillantGatewayRuntime(hass, entry)
    await runtime.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down a configured EEBUS gateway."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime: VaillantGatewayRuntime = hass.data[DOMAIN].pop(entry.entry_id)
        await runtime.async_stop()
    return unloaded
