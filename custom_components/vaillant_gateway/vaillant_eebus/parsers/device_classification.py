"""Parsers for the SPINE DeviceClassification feature.

DeviceClassification carries the device's own identity — manufacturer data
(model name / code / serial) on the appliance entity and user data (the label
the owner set in the app) on heating zones. It is read-only metadata, like
ElectricalConnection. Unlike the list-shaped feature payloads, these two
functions each return a single object.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ._common import str_or_none

# Manufacturer-data fields we surface (everything the gateway is known to send).
_MANUFACTURER_FIELDS = (
    "deviceName",
    "deviceCode",
    "serialNumber",
    "vendorName",
    "brandName",
    "powerSource",
)
# User-data fields we surface.
_USER_FIELDS = ("userLabel", "deviceName")


def _first_obj(cmd: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    """Return the object under ``key``, tolerating an array-wrapped single item.

    SPINE peers sometimes wrap a single object as a 1-element list; accept both
    ``{key: {...}}`` and ``{key: [{...}]}``.
    """
    val = cmd.get(key)
    if isinstance(val, dict):
        return val
    if isinstance(val, list):
        for item in val:
            if isinstance(item, dict):
                return item
    return None


def _pick(obj: Dict[str, Any], fields: tuple[str, ...]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for field in fields:
        value = str_or_none(obj.get(field))
        if value is not None:
            out[field] = value
    return out


def parse_device_classification_manufacturer_data(cmd: Dict[str, Any]) -> Dict[str, Any]:
    """Parse deviceClassificationManufacturerData → {deviceName, deviceCode, …}.

    Returns only the string fields that were present; ``{}`` on a malformed or
    empty payload.
    """
    obj = _first_obj(cmd, "deviceClassificationManufacturerData")
    if obj is None:
        return {}
    return _pick(obj, _MANUFACTURER_FIELDS)


def parse_device_classification_user_data(cmd: Dict[str, Any]) -> Dict[str, Any]:
    """Parse deviceClassificationUserData → {userLabel, deviceName}.

    Returns only the string fields that were present; ``{}`` when the entity
    carries no user label.
    """
    obj = _first_obj(cmd, "deviceClassificationUserData")
    if obj is None:
        return {}
    return _pick(obj, _USER_FIELDS)
