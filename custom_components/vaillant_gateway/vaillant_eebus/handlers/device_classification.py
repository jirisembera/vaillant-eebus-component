"""Handler for the SPINE DeviceClassification feature.

DeviceClassification is read-only (no subscription, no value reads) — like
ElectricalConnection, it exposes static metadata read once during bring-up.
Here it is the device's own identity: manufacturer data (model name / code /
serial) on the appliance entity and user data (the owner's app label) on
heating zones. The captured maps feed :func:`vaillant_eebus.topology.build_topology`,
which surfaces them on :class:`vaillant_eebus.topology.EntityInfo` and as the
device-level :class:`vaillant_eebus.topology.DeviceIdentity`.
"""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Tuple

from ..parsers import (
    extract_supported_functions_by_type,
    parse_device_classification_manufacturer_data,
    parse_device_classification_user_data,
)
from ..spine import SpineAddress, SpineChannel
from ..spine_requests import request_remote_functions
from .base import FeatureHandler

logger = logging.getLogger(__name__)


class DeviceClassificationHandler(FeatureHandler):
    feature_type: ClassVar[str] = "DeviceClassification"
    handled_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "deviceClassificationManufacturerData",
            "deviceClassificationUserData",
        }
    )
    description_cmd_keys: ClassVar[FrozenSet[str]] = frozenset(
        {
            "deviceClassificationManufacturerData",
            "deviceClassificationUserData",
        }
    )
    value_cmd_keys: ClassVar[FrozenSet[str]] = frozenset()
    subscribe_on_kickoff: ClassVar[bool] = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Both keyed by the entity tuple the reply came from (every entity that
        # advertises a DeviceClassification server gets its own manufacturer/user
        # data).
        self._manufacturer_by_entity: Dict[Tuple[int, ...], Dict[str, Any]] = {}
        self._user_by_entity: Dict[Tuple[int, ...], Dict[str, Any]] = {}
        # {(entity, feature): {advertised function names}} from detailed discovery,
        # so request_descriptions reads only what each server actually implements.
        self._supported_by_server: Dict[Tuple[Tuple[int, ...], int], FrozenSet[str]] = {}

    def set_servers_from_discovery(self, discovery: Dict[str, Any]) -> None:
        super().set_servers_from_discovery(discovery)
        self._supported_by_server = extract_supported_functions_by_type(
            discovery, self.feature_type
        )

    async def request_descriptions(self, channel: SpineChannel) -> None:
        """Read only the one description function each server advertises.

        Unlike every other feature, each DeviceClassification server implements
        just *one* of ``deviceClassificationManufacturerData`` /
        ``deviceClassificationUserData`` — appliance entities carry manufacturer
        data, heating zones carry user data. Reading both (as the base does)
        earns a rejected ``result`` for the unsupported half on every server.
        We consult the discovery's ``supportedFunction`` list and read just what
        is offered, so the bring-up stays quiet and the readiness counter reaches
        zero naturally. A server with no advertised-function info falls back to
        reading both, preserving the old best-effort behaviour.
        """
        if self.descriptions_ready.is_set():
            return
        reads = self._descriptions_to_read()
        self._pending_descriptions = sum(len(names) for _, names in reads)
        if self._pending_descriptions == 0:
            self.descriptions_ready.set()
            return
        for server, names in reads:
            await request_remote_functions(
                channel,
                local_client_feature=self._local_client_feature,
                remote_server_feature=server,
                function_names=names,
            )

    def _descriptions_to_read(self) -> List[Tuple[SpineAddress, List[str]]]:
        """Per server, the description functions to read — only those it advertises.

        A server with no advertised-function info (``supportedFunction`` empty or
        omitted) falls back to all ``description_cmd_keys``, preserving the old
        best-effort behaviour — we never under-read on missing metadata.
        """
        keys = sorted(self.description_cmd_keys)
        reads: List[Tuple[SpineAddress, List[str]]] = []
        for server in self._servers:
            if server.feature is None:
                continue
            supported = self._supported_by_server.get((server.entity, server.feature))
            names = [k for k in keys if k in supported] if supported else keys
            if names:
                reads.append((server, names))
        return reads

    @property
    def classification_map(self) -> Mapping[Tuple[int, ...], Dict[str, Dict[str, Any]]]:
        """Read-only {entity: {"manufacturer": {...}, "user": {...}}} for entities seen."""
        out: Dict[Tuple[int, ...], Dict[str, Dict[str, Any]]] = {}
        for ent, data in self._manufacturer_by_entity.items():
            out.setdefault(ent, {})["manufacturer"] = data
        for ent, data in self._user_by_entity.items():
            out.setdefault(ent, {})["user"] = data
        return MappingProxyType(out)

    def _handle(self, cmd_key: str, hdr: Dict[str, Any], cmd: Dict[str, Any]) -> None:
        addr = SpineAddress.from_raw(hdr.get("addressSource"))
        if addr is None:
            return
        ent = addr.entity
        if cmd_key == "deviceClassificationManufacturerData":
            data = parse_device_classification_manufacturer_data(cmd)
            self._manufacturer_by_entity[ent] = data
            logger.debug("✅ [DEVICECLASS] manufacturer data for entity=%s: %s", list(ent), data)
        elif cmd_key == "deviceClassificationUserData":
            data = parse_device_classification_user_data(cmd)
            self._user_by_entity[ent] = data
            logger.debug("✅ [DEVICECLASS] user data for entity=%s: %s", list(ent), data)
