"""Home Assistant-specific labelling (device_class / state_class).

Local copy of eebus_to_mqtt.ha_meta.guess_ha_metadata so this integration has no
runtime dependency on the MQTT bridge package (which pulls in paho-mqtt).
"""

from __future__ import annotations

from typing import Dict


def guess_ha_metadata(scope_type: str, unit: str) -> Dict[str, str]:
    """Best-effort mapping from a SPINE scope/unit to HA sensor metadata."""
    s = (scope_type or "").lower()
    u = (unit or "").strip()

    if "temperature" in s:
        return {"device_class": "temperature", "state_class": "measurement", "unit": u or "°C"}
    if "frequency" in s:
        return {"device_class": "frequency", "state_class": "measurement", "unit": u or "Hz"}
    if "power" in s:
        return {"device_class": "power", "state_class": "measurement", "unit": u or "W"}
    if "energy" in s:
        return {"device_class": "energy", "state_class": "total_increasing", "unit": u or "Wh"}
    if "current" in s:
        return {"device_class": "current", "state_class": "measurement", "unit": u or "A"}
    if "voltage" in s:
        return {"device_class": "voltage", "state_class": "measurement", "unit": u or "V"}
    return {"device_class": "", "state_class": "measurement", "unit": u}
