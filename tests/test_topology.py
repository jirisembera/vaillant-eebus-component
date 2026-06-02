"""Tests for build_topology and the Topology query helpers."""

from __future__ import annotations

from vaillant_eebus.keys import (
    hvac_mode_key,
    hvac_overrun_key,
    measurement_key,
    setpoint_key,
)
from vaillant_eebus.topology import Topology, TopologyMaps, build_topology


def test_build_topology_none_discovery():
    t = build_topology(discovery=None, device_address="dev-1")
    assert isinstance(t, Topology)
    assert t.device_address == "dev-1"
    assert t.entities == ()


def test_build_topology_entities_and_features(discovery):
    measurements = {
        ((1, 1), 1): {
            1: {"scopeType": "DHWTemperature", "unit": "degC", "measurementType": "temperature"}
        }
    }
    setpoints = {
        ((1, 1), 2): {
            3: {"setpointType": "dhw", "scopeType": "DHW", "unit": "degC", "description": "DHW"}
        }
    }
    sysfuncs = {((1, 1), 3): {7: {"systemFunctionType": "dhwLoading"}}}
    opmodes = {((1, 1), 3): {1: "auto", 2: "off"}}
    electrical = {(1, 1): {10: {"parameterId": 0, "phases": "a", "measurementType": "power"}}}

    t = build_topology(
        discovery=discovery,
        device_address="dev-1",
        maps=TopologyMaps(
            measurements_by_key=measurements,
            setpoints_by_key=setpoints,
            system_functions_by_key=sysfuncs,
            operation_modes_by_key=opmodes,
            electrical_params_by_entity=electrical,
        ),
    )

    # Entities sorted, DeviceInformation [1] before HeatPump [1, 1].
    assert [e.entity for e in t.entities] == [(1,), (1, 1)]

    # [1, 1] nests under [1]; [1] is the sole root. Same objects in both views.
    assert [e.entity for e in t.roots()] == [(1,)]
    assert [c.entity for c in t.entity((1,)).children] == [(1, 1)]
    assert t.entity((1,)).children[0] is t.entity((1, 1))
    assert t.entity((1, 1)).children == ()

    hp = t.entity((1, 1))
    assert hp is not None
    assert hp.entity_type == "HeatPumpAppliance"
    # Device entity carries no features in the fixture.
    assert t.entity((1,)).features == ()

    meas = t.feature((1, 1), 1)
    assert meas.feature_type == "Measurement"
    assert meas.role == "server"
    assert meas.measurements[0].scope_type == "DHWTemperature"
    assert meas.measurements[0].unit == "degC"
    # Each description carries the stable key of the value it will emit — equal
    # to what the handler computes, so consumers navigate desc → value directly.
    assert meas.measurements[0].key == measurement_key(
        scope_type="DHWTemperature", entity=(1, 1), feature=1, measurement_id=1
    )

    sp = t.feature((1, 1), 2)
    assert sp.setpoints[0].setpoint_id == 3
    assert sp.setpoints[0].setpoint_type == "dhw"
    assert sp.setpoints[0].description == "DHW"
    assert sp.setpoints[0].key == setpoint_key(
        scope_type="DHW", setpoint_type="dhw", entity=(1, 1), feature=2, setpoint_id=3
    )

    hvac = t.feature((1, 1), 3)
    assert {m.operation_mode_id: m.operation_mode_type for m in hvac.operation_modes} == {
        1: "auto",
        2: "off",
    }
    assert hvac.system_functions[0].system_function_type == "dhwLoading"
    assert hvac.system_functions[0].key == hvac_mode_key(
        entity=(1, 1), feature=3, system_function_id=7
    )

    ec = t.feature((1, 1), 4)
    assert ec.electrical_parameters[0].measurement_id == 10
    assert ec.electrical_parameters[0].phases == "a"
    assert ec.electrical_parameters[0].parameter_id == 0


def test_topology_queries_return_none_for_unknown(discovery):
    t = build_topology(discovery=discovery, device_address="d")
    assert t.entity((9, 9)) is None
    assert t.feature((9, 9), 1) is None
    assert t.feature((1, 1), 999) is None


def test_build_topology_device_classification(discovery):
    t = build_topology(
        discovery=discovery,
        device_address="dev-1",
        maps=TopologyMaps(
            device_classification_by_entity={
                (1, 1): {
                    "manufacturer": {
                        "deviceName": "Example Heat Pump",
                        "deviceCode": "EX-100",
                        "serialNumber": "0000000001",
                        "vendorName": "Example Group",
                        "brandName": "Example",
                    }
                },
                # Label lives on the parent DeviceInformation entity in this fixture.
                (1,): {"user": {"userLabel": "Zone 1"}},
            },
        ),
    )

    hp = t.entity((1, 1))
    assert hp.device_name == "Example Heat Pump"
    assert hp.device_code == "EX-100"
    assert hp.serial_number == "0000000001"
    assert hp.brand_name == "Example"
    assert hp.vendor_name == "Example Group"

    # Device-level identity is distilled from the HeatPumpAppliance entity.
    identity = t.primary_identity()
    assert identity is not None
    assert identity.manufacturer == "Example"
    assert identity.model == "Example Heat Pump"
    assert identity.model_id == "EX-100"
    assert identity.serial == "0000000001"

    # The label on the parent resolves onto the child via the ancestor walk.
    assert t.entity((1,)).user_label == "Zone 1"
    assert t.nearest_user_label((1, 1)) == "Zone 1"


def test_primary_identity_and_label_absent_without_classification(discovery):
    t = build_topology(discovery=discovery, device_address="d")
    assert t.primary_identity() is None
    assert t.nearest_user_label((1, 1)) is None


def test_build_topology_setpoint_mode_type(discovery):
    # Two room setpoints (1, 3); the HVAC relation list ties on→[1], eco→[3]
    # (auto spans both). build_topology stamps each setpoint's distinctive mode.
    t = build_topology(
        discovery=discovery,
        device_address="dev-1",
        maps=TopologyMaps(
            setpoints_by_key={
                ((1, 1), 2): {
                    1: {"setpointType": "valueAbsolute", "scopeType": "roomAirTemperature"},
                    3: {"setpointType": "valueAbsolute", "scopeType": "roomAirTemperature"},
                }
            },
            operation_modes_by_key={((1, 1), 3): {0: "auto", 1: "on", 2: "off", 3: "eco"}},
            hvac_setpoint_relations_by_key={((1, 1), 3): {0: {0: [1, 3], 1: [1], 2: [], 3: [3]}}},
        ),
    )
    sps = {sd.setpoint_id: sd for sd in t.feature((1, 1), 2).setpoints}
    assert sps[1].mode_type == "on"
    assert sps[3].mode_type == "eco"


def test_build_topology_setpoint_constraints(discovery):
    # The device-reported DHW limits ride onto the SetpointDescription, and
    # setpoint_description(key) recovers it from just the stable update key.
    t = build_topology(
        discovery=discovery,
        device_address="dev-1",
        maps=TopologyMaps(
            setpoints_by_key={
                ((1, 1), 2): {
                    1: {"setpointType": "valueAbsolute", "scopeType": "dhwTemperature"},
                }
            },
            setpoint_constraints_by_key={
                ((1, 1), 2): {1: {"rangeMin": 35.0, "rangeMax": 70.0, "stepSize": 1.0}}
            },
        ),
    )
    (sd,) = t.feature((1, 1), 2).setpoints
    assert sd.range_min == 35.0
    assert sd.range_max == 70.0
    assert sd.step == 1.0

    key = setpoint_key(
        scope_type="dhwTemperature",
        setpoint_type="valueAbsolute",
        entity=(1, 1),
        feature=2,
        setpoint_id=1,
    )
    assert t.setpoint_description(key) is sd
    assert t.setpoint_description("nope") is None


def test_build_topology_setpoint_without_constraints_has_none_bounds(discovery):
    # No constraints map → the bounds stay None (entities fall back to defaults).
    t = build_topology(
        discovery=discovery,
        device_address="dev-1",
        maps=TopologyMaps(
            setpoints_by_key={((1, 1), 2): {1: {"scopeType": "dhwTemperature"}}},
        ),
    )
    (sd,) = t.feature((1, 1), 2).setpoints
    assert sd.range_min is None and sd.range_max is None and sd.step is None


def test_build_topology_overruns(discovery):
    # The DHW overrun (one-time boost) rides onto the HVAC feature, and
    # overrun_description(key) recovers it from just the stable update key.
    t = build_topology(
        discovery=discovery,
        device_address="dev-1",
        maps=TopologyMaps(
            overruns_by_key={
                ((1, 1), 3): {
                    0: {"overrunType": "oneTimeDhw", "affectedSystemFunctionId": [0]},
                }
            },
        ),
    )
    (ov,) = t.feature((1, 1), 3).overruns
    assert ov.overrun_id == 0
    assert ov.overrun_type == "oneTimeDhw"
    assert ov.affected_system_function_ids == (0,)

    key = hvac_overrun_key(entity=(1, 1), feature=3, overrun_id=0)
    assert ov.key == key
    assert t.overrun_description(key) is ov
    assert t.overrun_description("nope") is None


def test_build_topology_no_overruns_is_empty(discovery):
    t = build_topology(discovery=discovery, device_address="dev-1")
    hvac = t.feature((1, 1), 3)
    assert hvac is not None
    assert hvac.overruns == ()


def test_build_topology_single_setpoint_not_labelled(discovery):
    # DHW shape: one referenced setpoint shared by auto+on → nothing to
    # disambiguate, so no mode_type is stamped.
    t = build_topology(
        discovery=discovery,
        device_address="dev-1",
        maps=TopologyMaps(
            setpoints_by_key={((1, 1), 2): {1: {"scopeType": "dhwTemperature"}}},
            operation_modes_by_key={((1, 1), 3): {0: "auto", 1: "on", 2: "off"}},
            hvac_setpoint_relations_by_key={((1, 1), 3): {0: {0: [1], 1: [1], 2: []}}},
        ),
    )
    (sd,) = t.feature((1, 1), 2).setpoints
    assert sd.mode_type is None


def _disc(*addrs):
    """Minimal discovery with just the given entity addresses (no features)."""
    return {
        "entityInformation": [
            {
                "description": {
                    "entityAddress": {"entity": list(a)},
                    "entityType": "E" + "_".join(map(str, a)),
                }
            }
            for a in addrs
        ],
        "featureInformation": [],
    }


def test_entity_tree_nesting_and_roots():
    # Real-device-shaped: 3 → 3,1 ; 5 → 5,1 → 5,1,1 ; 6 standalone.
    t = build_topology(discovery=_disc((5,), (5, 1), (5, 1, 1), (3,), (3, 1), (6,)))

    # Flat index stays sorted and complete.
    assert [e.entity for e in t.entities] == [(3,), (3, 1), (5,), (5, 1), (5, 1, 1), (6,)]
    # Roots are the top-level (single-element) addresses, sorted.
    assert [e.entity for e in t.roots()] == [(3,), (5,), (6,)]

    # The 5 → 5,1 → 5,1,1 chain, walked through children.
    five = t.entity((5,))
    assert [c.entity for c in five.children] == [(5, 1)]
    assert [c.entity for c in five.children[0].children] == [(5, 1, 1)]
    assert five.children[0].children[0].children == ()
    # Same EntityInfo object appears in the flat index and the tree.
    assert t.entity((5, 1)) is five.children[0]

    assert [c.entity for c in t.entity((3,)).children] == [(3, 1)]
    assert t.entity((6,)).children == ()


def test_entity_tree_tolerates_missing_intermediate():
    # [7, 1] absent → [7, 1, 1] attaches to the nearest present ancestor, [7].
    t = build_topology(discovery=_disc((7,), (7, 1, 1)))
    assert [e.entity for e in t.roots()] == [(7,)]
    assert [c.entity for c in t.entity((7,)).children] == [(7, 1, 1)]


def test_entity_tree_orphan_without_ancestor_is_root():
    # [8, 1] present but its parent [8] is not → [8, 1] is itself a root.
    t = build_topology(discovery=_disc((8, 1), (9,)))
    assert [e.entity for e in t.roots()] == [(8, 1), (9,)]
    assert t.entity((8, 1)).children == ()
