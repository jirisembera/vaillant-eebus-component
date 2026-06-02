"""Shared HVAC-mode behaviour for the grouped climate / water_heater entities.

Both wrap a structurally identical group (temperature + setpoint + HVAC keys)
and expose the same device flags carried on the HVAC system-function row
(``mode_changeable`` / ``boost_active``) plus the same write-guard. This mixin
holds that shared surface so the two platforms don't duplicate it.

The host entity sets :attr:`_hvac_key` and inherits :attr:`_runtime` from
:class:`VaillantGatewayEntity`; list it before the HA entity base so its
``extra_state_attributes`` wins in the MRO.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from homeassistant.exceptions import HomeAssistantError

from .coordinator import VaillantGatewayRuntime
from .vaillant_eebus import HvacModeUpdate


class GroupedHvacModeEntity:
    """Mixin exposing the HVAC system-function flags + mode-change guard."""

    _runtime: VaillantGatewayRuntime
    _hvac_key: str

    def _hvac_update(self) -> Optional[HvacModeUpdate]:
        """The latest HVAC-mode update for this group, if one has arrived."""
        update = self._runtime.latest.get(self._hvac_key)
        return update if isinstance(update, HvacModeUpdate) else None

    @property
    def extra_state_attributes(self) -> Optional[Dict[str, Any]]:
        """Surface the device's HVAC flags carried on the system-function row."""
        update = self._hvac_update()
        if update is None:
            return None
        attrs: Dict[str, Any] = {}
        if update.mode_changeable is not None:
            attrs["mode_changeable"] = update.mode_changeable
        if update.overrun_active is not None:
            attrs["boost_active"] = update.overrun_active
        return attrs or None

    def _ensure_mode_changeable(self) -> None:
        """Raise when the device currently forbids changing the operation mode."""
        update = self._hvac_update()
        if update is not None and update.mode_changeable is False:
            raise HomeAssistantError("Operation mode is currently not changeable on the device")
