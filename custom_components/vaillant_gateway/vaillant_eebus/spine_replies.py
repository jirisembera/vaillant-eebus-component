"""Local replies to SPINE read requests from the gateway.

Vaillant routinely reads NodeManagementDetailedDiscoveryData and
DeviceClassification* from us. We answer with minimal but valid descriptors.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from .ship import send_ship_data
from .spine import (
    LOCAL_CEM_ENTITY,
    LOCAL_DEVICE_ENTITY,
    LOCAL_FEATURE_DEVICE_CLASSIFICATION,
    LOCAL_FEATURE_ELECTRICAL,
    LOCAL_FEATURE_HVAC,
    LOCAL_FEATURE_MEASUREMENT,
    LOCAL_FEATURE_NODE_MANAGEMENT,
    LOCAL_FEATURE_SENSING,
    LOCAL_FEATURE_SETPOINT,
    make_reply_addresses,
)
from .utils import MsgCounter

logger = logging.getLogger(__name__)


def build_local_detailed_discovery(local_device_address: str) -> Dict[str, Any]:
    """Minimal NodeManagementDetailedDiscoveryData reply.

    Enough to let a remote device identify us and keep the session alive.
    """
    return {
        "specificationVersionList": {"specificationVersion": ["1.3.0"]},
        "deviceInformation": {
            "description": {
                "deviceAddress": {"device": local_device_address},
                "deviceType": "EnergyManagementSystem",
                "featureSet": "smart",
                "brandName": "Python",
                "deviceModel": "SHIP-Layer1",
                "serialNumber": local_device_address,
                "deviceCode": "python-ship",
            }
        },
        "entityInformation": [
            {
                "description": {
                    "entityAddress": {"entity": [LOCAL_DEVICE_ENTITY]},
                    "entityType": "DeviceInformation",
                    "description": "DeviceInformation",
                }
            },
            {
                "description": {
                    "entityAddress": {"entity": [LOCAL_CEM_ENTITY]},
                    "entityType": "CEM",
                    "description": "CEM",
                }
            },
        ],
        "featureInformation": [
            {
                "description": {
                    "featureAddress": {
                        "entity": [LOCAL_DEVICE_ENTITY],
                        "feature": LOCAL_FEATURE_NODE_MANAGEMENT,
                    },
                    "featureType": "NodeManagement",
                    "role": "special",
                    "description": "NodeManagement",
                }
            },
            {
                "description": {
                    "featureAddress": {
                        "entity": [LOCAL_DEVICE_ENTITY],
                        "feature": LOCAL_FEATURE_DEVICE_CLASSIFICATION,
                    },
                    "featureType": "DeviceClassification",
                    "role": "server",
                    "description": "DeviceClassification",
                }
            },
            {
                "description": {
                    "featureAddress": {
                        "entity": [LOCAL_CEM_ENTITY],
                        "feature": LOCAL_FEATURE_MEASUREMENT,
                    },
                    "featureType": "Measurement",
                    "role": "client",
                    "description": "MeasurementClient",
                }
            },
            {
                "description": {
                    "featureAddress": {
                        "entity": [LOCAL_CEM_ENTITY],
                        "feature": LOCAL_FEATURE_SENSING,
                    },
                    "featureType": "Sensing",
                    "role": "client",
                    "description": "SensingClient",
                }
            },
            {
                "description": {
                    "featureAddress": {
                        "entity": [LOCAL_CEM_ENTITY],
                        "feature": LOCAL_FEATURE_HVAC,
                    },
                    "featureType": "HVAC",
                    "role": "client",
                    "description": "HVACClient",
                }
            },
            {
                "description": {
                    "featureAddress": {
                        "entity": [LOCAL_CEM_ENTITY],
                        "feature": LOCAL_FEATURE_SETPOINT,
                    },
                    "featureType": "Setpoint",
                    "role": "client",
                    "description": "SetpointClient",
                }
            },
            {
                "description": {
                    "featureAddress": {
                        "entity": [LOCAL_CEM_ENTITY],
                        "feature": LOCAL_FEATURE_ELECTRICAL,
                    },
                    "featureType": "ElectricalConnection",
                    "role": "client",
                    "description": "ElectricalConnectionClient",
                }
            },
        ],
    }


def build_device_classification_manufacturer_data(local_device_address: str) -> Dict[str, Any]:
    return {
        "deviceName": "SHIP Python Client",
        "deviceCode": "python-ship",
        "brandName": "Python",
        "powerSource": "mains3Phase",
        "serialNumber": local_device_address,
    }


def build_device_classification_user_data() -> Dict[str, Any]:
    return {"deviceName": "SHIP Python Client"}


async def _send_reply(
    ws,
    *,
    request_header: Dict[str, Any],
    local_device_address: str,
    msg_counter: MsgCounter,
    cmd_payload: Dict[str, Any],
    log_label: str,
) -> None:
    ref = request_header.get("msgCounter")
    if ref is None:
        logger.warning("[SPINE] No msgCounter in request header → cannot reply (%s)", log_label)
        return

    addresses = make_reply_addresses(request_header, local_device_address=local_device_address)
    if addresses is None:
        logger.warning(
            "[SPINE] Request without addressSource/addressDestination → cannot reply (%s)",
            log_label,
        )
        return

    address_source, address_destination = addresses

    reply_datagram: Dict[str, Any] = {
        "datagram": {
            "header": {
                "specificationVersion": request_header.get("specificationVersion", "1.3.0"),
                "addressSource": address_source,
                "addressDestination": address_destination,
                "msgCounter": await msg_counter.next(),
                "msgCounterReference": ref,
                "cmdClassifier": "reply",
            },
            "payload": {"cmd": [cmd_payload]},
        }
    }

    await send_ship_data(ws, reply_datagram)
    logger.debug("📤 [SPINE] Reply sent: %s", log_label)


async def reply_node_management_detailed_discovery(
    ws,
    *,
    request_header: Dict[str, Any],
    local_device_address: str,
    msg_counter: MsgCounter,
) -> None:
    await _send_reply(
        ws,
        request_header=request_header,
        local_device_address=local_device_address,
        msg_counter=msg_counter,
        cmd_payload={
            "nodeManagementDetailedDiscoveryData": build_local_detailed_discovery(
                local_device_address
            ),
            "function": "nodeManagementDetailedDiscoveryData",
        },
        log_label="nodeManagementDetailedDiscoveryData",
    )


async def reply_device_classification_manufacturer_data(
    ws,
    *,
    request_header: Dict[str, Any],
    local_device_address: str,
    msg_counter: MsgCounter,
) -> None:
    await _send_reply(
        ws,
        request_header=request_header,
        local_device_address=local_device_address,
        msg_counter=msg_counter,
        cmd_payload={
            "deviceClassificationManufacturerData": build_device_classification_manufacturer_data(
                local_device_address
            ),
        },
        log_label="deviceClassificationManufacturerData",
    )


async def reply_device_classification_user_data(
    ws,
    *,
    request_header: Dict[str, Any],
    local_device_address: str,
    msg_counter: MsgCounter,
) -> None:
    await _send_reply(
        ws,
        request_header=request_header,
        local_device_address=local_device_address,
        msg_counter=msg_counter,
        cmd_payload={"deviceClassificationUserData": build_device_classification_user_data()},
        log_label="deviceClassificationUserData",
    )


async def handle_spine_read(
    ws,
    *,
    request_header: Dict[str, Any],
    cmd: Dict[str, Any],
    local_device_address: str,
    msg_counter: MsgCounter,
) -> None:
    """Handle SPINE cmdClassifier='read' with minimal required replies."""
    if "nodeManagementDetailedDiscoveryData" in cmd:
        await reply_node_management_detailed_discovery(
            ws,
            request_header=request_header,
            local_device_address=local_device_address,
            msg_counter=msg_counter,
        )
        return

    if "deviceClassificationManufacturerData" in cmd:
        await reply_device_classification_manufacturer_data(
            ws,
            request_header=request_header,
            local_device_address=local_device_address,
            msg_counter=msg_counter,
        )
        return

    if "deviceClassificationUserData" in cmd:
        await reply_device_classification_user_data(
            ws,
            request_header=request_header,
            local_device_address=local_device_address,
            msg_counter=msg_counter,
        )
        return

    logger.warning("[SPINE] Unhandled read cmd keys: %s", list(cmd.keys()))
