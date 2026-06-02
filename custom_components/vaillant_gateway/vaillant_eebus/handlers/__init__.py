"""Per-SPINE-feature handlers consumed by :class:`vaillant_eebus.client.EebusClient`."""

from .base import FeatureHandler, FeatureKey, OnUpdate
from .device_classification import DeviceClassificationHandler
from .electrical import ElectricalHandler
from .hvac import HVACHandler
from .measurement import MeasurementHandler
from .setpoint import SetpointHandler

__all__ = [
    "DeviceClassificationHandler",
    "ElectricalHandler",
    "FeatureHandler",
    "FeatureKey",
    "HVACHandler",
    "MeasurementHandler",
    "OnUpdate",
    "SetpointHandler",
]
