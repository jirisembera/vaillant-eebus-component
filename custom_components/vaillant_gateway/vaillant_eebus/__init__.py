"""Diagnostic SHIP/SPINE client library for Vaillant EEBUS devices.

Public API:

- :class:`EebusClient`     — high-level async client; exposes ``.values``,
                             ``.on_change(cb)`` and ``.updates()``.
- :class:`EebusPairing`    — one-shot trust handshake (HELLO ``pending``).
- :class:`Update` and subclasses :class:`MeasurementUpdate`,
  :class:`HvacModeUpdate`, :class:`SetpointUpdate` — event records emitted
  by the client.

The MQTT/Home-Assistant bridge lives in the :mod:`eebus_to_mqtt` tool (``tools/``).
"""

from __future__ import annotations

from .client import EebusClient
from .connection import (
    Gateway,
    LocalNode,
    discover_gateway,
    discover_gateways,
    open_node,
    private_aiozc,
    select_gateway,
)
from .errors import EebusError, EebusWriteError
from .events import (
    HvacModeUpdate,
    HvacOverrunUpdate,
    MeasurementUpdate,
    SetpointUpdate,
    Update,
)
from .pairing import EebusPairing
from .topology import (
    DeviceIdentity,
    ElectricalParameterDescription,
    EntityInfo,
    FeatureInfo,
    HvacOperationModeDescription,
    HvacOverrunDescription,
    HvacSystemFunctionDescription,
    MeasurementDescription,
    SetpointDescription,
    Topology,
    TopologyMaps,
)

__all__ = [
    "EebusClient",
    "EebusPairing",
    "EebusError",
    "EebusWriteError",
    "Gateway",
    "LocalNode",
    "discover_gateway",
    "discover_gateways",
    "select_gateway",
    "open_node",
    "private_aiozc",
    "Update",
    "MeasurementUpdate",
    "HvacModeUpdate",
    "HvacOverrunUpdate",
    "SetpointUpdate",
    "Topology",
    "TopologyMaps",
    "DeviceIdentity",
    "EntityInfo",
    "FeatureInfo",
    "MeasurementDescription",
    "SetpointDescription",
    "HvacSystemFunctionDescription",
    "HvacOperationModeDescription",
    "HvacOverrunDescription",
    "ElectricalParameterDescription",
]
