"""Tests for the detailed-discovery extractors."""

from __future__ import annotations

from vaillant_eebus.parsers import (
    entity_addr_list,
    extract_entities,
    extract_heat_pump_entity,
    extract_servers_by_type,
    extract_supported_functions_by_type,
)
from vaillant_eebus.spine import SpineAddress


def test_entity_addr_list():
    assert entity_addr_list({"entity": [1, 1]}) == [1, 1]
    assert entity_addr_list({"entity": []}) is None
    assert entity_addr_list({"entity": [1, "x"]}) is None
    assert entity_addr_list({}) is None
    assert entity_addr_list("nope") is None


def test_extract_heat_pump_entity(discovery):
    assert extract_heat_pump_entity(discovery) == {"entity": [1, 1]}


def test_extract_heat_pump_entity_no_heatpump():
    disc = {
        "entityInformation": [
            {"description": {"entityAddress": {"entity": [1]}, "entityType": "CEM"}}
        ],
        "featureInformation": [],
    }
    assert extract_heat_pump_entity(disc) is None


def test_extract_entities_sorted(discovery):
    ents = extract_entities(discovery)
    # [1] sorts before [1, 1]
    assert [e["entity"] for e in ents] == [[1], [1, 1]]
    hp = next(e for e in ents if e["entity"] == [1, 1])
    assert hp["entityType"] == "HeatPumpAppliance"
    assert hp["description"] == "Heat pump"


def test_extract_servers_by_type_filters_role_and_sorts():
    disc = {
        "featureInformation": [
            {
                "description": {
                    "featureAddress": {"entity": [1, 1], "feature": 5},
                    "featureType": "Measurement",
                    "role": "server",
                }
            },
            {
                "description": {
                    "featureAddress": {"entity": [1, 1], "feature": 1},
                    "featureType": "Measurement",
                    "role": "server",
                }
            },
            {
                "description": {
                    "featureAddress": {"entity": [1, 1], "feature": 9},
                    "featureType": "Measurement",
                    "role": "client",  # filtered out
                }
            },
            {
                "description": {
                    "featureAddress": {"entity": [1, 1], "feature": 2},
                    "featureType": "Setpoint",
                    "role": "server",  # wrong type
                }
            },
        ]
    }
    servers = extract_servers_by_type(disc, "Measurement")
    assert servers == [
        SpineAddress((1, 1), 1),
        SpineAddress((1, 1), 5),
    ]


def test_extract_servers_by_type_empty_when_absent():
    assert extract_servers_by_type({}, "Measurement") == []


def test_extract_supported_functions_by_type():
    disc = {
        "featureInformation": [
            {
                "description": {
                    "featureAddress": {"entity": [3], "feature": 4},
                    "featureType": "DeviceClassification",
                    "role": "server",
                    "supportedFunction": [{"function": "deviceClassificationManufacturerData"}],
                }
            },
            {
                "description": {
                    "featureAddress": {"entity": [5, 1], "feature": 4},
                    "featureType": "DeviceClassification",
                    "role": "server",
                    # array-wrapping can collapse a lone entry to a bare dict
                    "supportedFunction": {"function": "deviceClassificationUserData"},
                }
            },
            {
                "description": {
                    "featureAddress": {"entity": [1, 1], "feature": 1},
                    "featureType": "Measurement",
                    "role": "server",  # wrong type → excluded
                }
            },
        ]
    }
    mapping = extract_supported_functions_by_type(disc, "DeviceClassification")
    assert mapping == {
        ((3,), 4): frozenset({"deviceClassificationManufacturerData"}),
        ((5, 1), 4): frozenset({"deviceClassificationUserData"}),
    }


def test_extract_supported_functions_missing_list_is_empty_set():
    disc = {
        "featureInformation": [
            {
                "description": {
                    "featureAddress": {"entity": [0], "feature": 1},
                    "featureType": "DeviceClassification",
                    "role": "server",
                    # no supportedFunction key at all
                }
            }
        ]
    }
    assert extract_supported_functions_by_type(disc, "DeviceClassification") == {
        ((0,), 1): frozenset()
    }
