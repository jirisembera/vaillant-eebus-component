"""Bridge :class:`vaillant_eebus.client.EebusClient` updates to Home Assistant MQTT.

Subscribes to a client's updates via ``on_change`` and republishes them as HA
Discovery sensors. Re-published discovery configs are tracked per-key so the
bridge can refresh names/metadata when the underlying ``Update`` changes
shape (e.g. once per-phase ElectricalConnection info becomes available).
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from vaillant_eebus.client import EebusClient
from vaillant_eebus.events import HvacModeUpdate, MeasurementUpdate, SetpointUpdate, Update
from vaillant_eebus.naming import (
    friendly_hvac_function_name,
    friendly_sensor_name,
    friendly_setpoint_name,
)
from vaillant_eebus.utils import env_str, slug

from .ha_meta import guess_ha_metadata
from .publisher import HAMqttPublisher

logger = logging.getLogger(__name__)


class HABridge:
    """Push EebusClient updates to a Home Assistant MQTT broker.

    Usage::

        async with EebusClient(...) as hp, HABridge(client=hp) as bridge:
            await hp.wait_closed()
    """

    def __init__(
        self,
        *,
        client: EebusClient,
        device_id: Optional[str] = None,
        device_name: Optional[str] = None,
        publisher: Optional[HAMqttPublisher] = None,
    ) -> None:
        self._client = client
        self._unsubscribe = None

        if publisher is None:
            resolved_id = device_id or env_str("HA_DEVICE_ID", "eebus_gateway")
            resolved_name = device_name or env_str("HA_DEVICE_NAME", "EEBUS HeatPump")
            publisher = HAMqttPublisher(
                device_id=slug(resolved_id),
                device_name=resolved_name,
            )
        self._publisher = publisher

        # Per-key cache of the friendly name we last announced via Discovery, so
        # we can detect when the name needs refreshing (e.g. phase info arrived).
        self._announced_names: Dict[str, str] = {}

    async def __aenter__(self) -> "HABridge":
        self._publisher.connect()
        self._unsubscribe = self._client.on_change(self._handle_update)
        # Backfill from anything the client already saw before we attached.
        for update in list(self._client.values.values()):
            self._handle_update(update)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        try:
            self._publisher.close()
        except Exception:
            logger.exception("[HA] publisher close failed")

    # -- Dispatch ----------------------------------------------------------

    def _handle_update(self, update: Update) -> None:
        try:
            if isinstance(update, MeasurementUpdate):
                self._publish_measurement(update)
            elif isinstance(update, SetpointUpdate):
                self._publish_setpoint(update)
            elif isinstance(update, HvacModeUpdate):
                self._publish_hvac_mode(update)
        except Exception:
            logger.exception("[HA] failed to publish %s", update.key)

    def _publish_measurement(self, u: MeasurementUpdate) -> None:
        if not isinstance(u.value, (int, float)):
            return
        name = friendly_sensor_name(
            u.scope_type,
            source_entity=u.source_entity,
            phase_info=u.phase,
        )
        meta = guess_ha_metadata(u.scope_type, u.unit)
        self._ensure_discovery(
            object_id=u.key,
            name=name,
            unit=meta.get("unit", u.unit),
            device_class=meta.get("device_class", ""),
            state_class=meta.get("state_class", "measurement"),
        )
        self._publisher.publish_state(object_id=u.key, value=float(u.value))

    def _publish_setpoint(self, u: SetpointUpdate) -> None:
        if not isinstance(u.value, (int, float)):
            return
        name = friendly_setpoint_name(u.scope_type)
        self._ensure_discovery(
            object_id=u.key,
            name=name,
            unit=u.unit or "°C",
            device_class="temperature",
            state_class="measurement",
        )
        self._publisher.publish_state(object_id=u.key, value=float(u.value))

    def _publish_hvac_mode(self, u: HvacModeUpdate) -> None:
        name = friendly_hvac_function_name(
            u.system_function_type,
            system_function_id=u.system_function_id,
        )
        self._ensure_discovery(
            object_id=u.key,
            name=f"{name} (sf{u.system_function_id})",
            unit="",
            device_class="",
            state_class="",
        )
        self._publisher.publish_state_text(
            object_id=u.key,
            value=str(u.mode) if u.mode is not None else "unknown",
        )

    def _ensure_discovery(
        self,
        *,
        object_id: str,
        name: str,
        unit: str,
        device_class: str,
        state_class: str,
    ) -> None:
        """Publish discovery, or re-publish if the friendly name has changed."""
        prev = self._announced_names.get(object_id)
        if prev is not None and prev != name:
            # Name has changed (e.g. phase info now available). Drop the cached
            # publication record so the publisher re-sends the discovery config.
            self._publisher.forget_discovery(object_id)
        self._publisher.ensure_discovery(
            object_id=object_id,
            name=name,
            unit=unit,
            device_class=device_class,
            state_class=state_class,
        )
        self._announced_names[object_id] = name
