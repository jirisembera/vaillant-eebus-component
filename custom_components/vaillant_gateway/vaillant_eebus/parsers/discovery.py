"""Extractors for the SPINE detailed-discovery reply."""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from ..spine import SpineAddress


def extract_heat_pump_entity(discovery: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the ``HeatPumpAppliance`` entity address dict, or ``None`` if absent."""
    entity_info = discovery.get("entityInformation")
    if not isinstance(entity_info, list):
        return None
    for item in entity_info:
        if not isinstance(item, dict):
            continue
        desc = item.get("description")
        if not isinstance(desc, dict):
            continue
        if desc.get("entityType") == "HeatPumpAppliance":
            ent_addr = desc.get("entityAddress")
            if isinstance(ent_addr, dict):
                return ent_addr
    return None


def entity_addr_list(entity_address: Any) -> Optional[List[int]]:
    addr = SpineAddress.from_raw(entity_address)
    return list(addr.entity) if addr is not None else None


def extract_entities(discovery: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    entity_info = discovery.get("entityInformation")
    if not isinstance(entity_info, list):
        return out
    for item in entity_info:
        if not isinstance(item, dict):
            continue
        desc = item.get("description")
        if not isinstance(desc, dict):
            continue
        ent_addr = entity_addr_list(desc.get("entityAddress"))
        if ent_addr is None:
            continue
        out.append(
            {
                "entity": ent_addr,
                "entityType": desc.get("entityType"),
                "description": desc.get("description"),
            }
        )
    out.sort(key=lambda d: d.get("entity") or [])
    return out


def extract_servers_by_type(discovery: Dict[str, Any], feature_type: str) -> List[SpineAddress]:
    """Return all role=server features matching `feature_type`, sorted by (entity, feature).

    Each is a device-less :class:`SpineAddress` (entity + feature); the session
    binds the connected gateway device when it reads/subscribes/writes.
    """
    servers: List[SpineAddress] = []
    feature_info = discovery.get("featureInformation")
    if not isinstance(feature_info, list):
        return servers
    for item in feature_info:
        if not isinstance(item, dict):
            continue
        desc = item.get("description")
        if not isinstance(desc, dict):
            continue
        if desc.get("role") != "server":
            continue
        if desc.get("featureType") != feature_type:
            continue
        addr = SpineAddress.from_raw(desc.get("featureAddress"))
        if addr is None or addr.feature is None:
            continue
        servers.append(addr)
    servers.sort(key=lambda a: (a.entity, a.feature or 0))
    return servers


def _supported_function_names(supported: Any) -> FrozenSet[str]:
    """Pull the ``function`` names out of a feature's ``supportedFunction`` list.

    Tolerates the array-wrapped shape collapsing a lone entry to a bare dict.
    """
    if isinstance(supported, dict):
        supported = [supported]
    if not isinstance(supported, list):
        return frozenset()
    return frozenset(
        entry["function"]
        for entry in supported
        if isinstance(entry, dict) and isinstance(entry.get("function"), str)
    )


def extract_supported_functions_by_type(
    discovery: Dict[str, Any], feature_type: str
) -> Dict[Tuple[Tuple[int, ...], int], FrozenSet[str]]:
    """Map each role=server feature of ``feature_type`` to the functions it offers.

    Keyed by ``(entity tuple, feature id)`` — the same identity
    :func:`extract_servers_by_type` produces — so a handler can read only the
    functions a server advertises under ``supportedFunction`` instead of probing
    every candidate and collecting a rejected ``result`` for the unsupported
    ones. A feature that omits ``supportedFunction`` maps to an empty set (the
    caller decides the fallback).
    """
    out: Dict[Tuple[Tuple[int, ...], int], FrozenSet[str]] = {}
    feature_info = discovery.get("featureInformation")
    if not isinstance(feature_info, list):
        return out
    for item in feature_info:
        if not isinstance(item, dict):
            continue
        desc = item.get("description")
        if not isinstance(desc, dict):
            continue
        if desc.get("role") != "server" or desc.get("featureType") != feature_type:
            continue
        addr = SpineAddress.from_raw(desc.get("featureAddress"))
        if addr is None or addr.feature is None:
            continue
        out[(addr.entity, addr.feature)] = _supported_function_names(desc.get("supportedFunction"))
    return out
