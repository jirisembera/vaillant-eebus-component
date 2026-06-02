"""Home Assistant MQTT bridge for the :mod:`vaillant_eebus` comm library.

Subscribes to :class:`vaillant_eebus.client.EebusClient` updates and republishes them
as Home Assistant MQTT Discovery sensors.
"""

from __future__ import annotations

from .bridge import HABridge

__all__ = ["HABridge"]
