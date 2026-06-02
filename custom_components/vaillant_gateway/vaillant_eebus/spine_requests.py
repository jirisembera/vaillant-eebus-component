"""Outgoing SPINE reads and subscription calls to the gateway."""

from __future__ import annotations

import logging
from typing import List

from .spine import SpineAddress, SpineChannel, send_spine_call, send_spine_read, spine_addr

logger = logging.getLogger(__name__)


async def request_remote_detailed_discovery(channel: SpineChannel) -> None:
    src = spine_addr(device=channel.local_device_address, entity=0, feature=0)
    dst = spine_addr(device=channel.remote_device_address, entity=0, feature=0)
    await send_spine_read(
        channel.ws,
        address_source=src,
        address_destination=dst,
        cmd={"nodeManagementDetailedDiscoveryData": {}},
        msg_counter=channel.msg_counter,
    )
    logger.debug("📤 [SPINE] Read sent: nodeManagementDetailedDiscoveryData")


async def request_remote_functions(
    channel: SpineChannel,
    *,
    local_client_feature: SpineAddress,
    remote_server_feature: SpineAddress,
    function_names: List[str],
) -> None:
    """Send several SPINE read requests against one remote feature."""
    if remote_server_feature.feature is None:
        return
    dst = remote_server_feature.with_device(channel.remote_device_address)
    for func_name in function_names:
        await send_spine_read(
            channel.ws,
            address_source=local_client_feature,
            address_destination=dst,
            cmd={func_name: {}},
            msg_counter=channel.msg_counter,
            ack_request=True,
        )


async def subscribe_remote_feature(
    channel: SpineChannel,
    *,
    local_client_feature: SpineAddress,
    remote_server_feature: SpineAddress,
    server_feature_type: str,
) -> None:
    """Subscribe a local client feature to a remote server feature via NodeManagement."""
    if remote_server_feature.feature is None:
        return

    src_nm = spine_addr(device=channel.local_device_address, entity=0, feature=0)
    dst_nm = spine_addr(device=channel.remote_device_address, entity=0, feature=0)
    remote_addr = remote_server_feature.with_device(channel.remote_device_address)
    sub_call = {
        "subscriptionRequest": {
            "clientAddress": local_client_feature.to_raw(),
            "serverAddress": remote_addr.to_raw(),
            "serverFeatureType": server_feature_type,
        }
    }
    await send_spine_call(
        channel.ws,
        address_source=src_nm,
        address_destination=dst_nm,
        cmd={"nodeManagementSubscriptionRequestCall": sub_call},
        msg_counter=channel.msg_counter,
        ack_request=True,
    )
