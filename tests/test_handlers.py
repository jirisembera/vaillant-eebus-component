"""Pure-logic tests for the per-feature handlers.

The handlers sit on top of the (separately tested) parsers and add the logic
that actually matters to consumers: stable-key generation, ``Update`` emission,
the Measurement↔ElectricalConnection phase wiring, the description/value
readiness latching that drives the linear bring-up, and the write-frame
builders. All of that is synchronous, so it is exercised here without a
websocket or an event loop — the async send/subscribe methods are out of scope
for this suite.
"""

from __future__ import annotations

import pytest
from vaillant_eebus.events import (
    HvacModeUpdate,
    HvacOverrunUpdate,
    MeasurementUpdate,
    SetpointUpdate,
)
from vaillant_eebus.handlers import (
    DeviceClassificationHandler,
    ElectricalHandler,
    HVACHandler,
    MeasurementHandler,
    SetpointHandler,
)
from vaillant_eebus.spine import SpineAddress, spine_addr


def _reply(entity, feature):
    return {"cmdClassifier": "reply", "addressSource": {"entity": list(entity), "feature": feature}}


def _notify(entity, feature):
    return {
        "cmdClassifier": "notify",
        "addressSource": {"entity": list(entity), "feature": feature},
    }


def _setpoint(on_update=lambda u: None):
    return SetpointHandler(
        local_client_feature=spine_addr(device="d:_i:1_me", entity=1, feature=4),
        on_update=on_update,
    )


def _hvac(on_update=lambda u: None):
    return HVACHandler(
        local_client_feature=spine_addr(device="d:_i:1_me", entity=1, feature=3),
        on_update=on_update,
    )


def _measurement(electrical=None, on_update=lambda u: None):
    return MeasurementHandler(
        local_client_feature=spine_addr(device="d:_i:1_me", entity=1, feature=1),
        on_update=on_update,
        electrical=electrical
        or ElectricalHandler(
            local_client_feature=spine_addr(device="d:_i:1_me", entity=1, feature=5),
            on_update=lambda u: None,
        ),
    )


def _device_classification(on_update=lambda u: None):
    return DeviceClassificationHandler(
        local_client_feature=spine_addr(device="d:_i:1_me", entity=1, feature=1),
        on_update=on_update,
    )


# --------------------------------------------------------------------------
# base.py: server lookup (raw-address parsing now lives in SpineAddress —
# covered in tests/test_spine_helpers.py)
# --------------------------------------------------------------------------


def test_find_server():
    sp = _setpoint()
    sp._servers = [SpineAddress((1, 1), 2), SpineAddress((4,), 2)]
    assert sp.find_server((1, 1), 2) == SpineAddress((1, 1), 2)
    assert sp.find_server((4,), 2) == SpineAddress((4,), 2)
    assert sp.find_server((9,), 2) is None  # unknown entity
    assert sp.find_server((1, 1), 9) is None  # unknown feature


# --------------------------------------------------------------------------
# dispatch → emitted Update (stable keys + phase wiring)
# --------------------------------------------------------------------------


def test_measurement_dispatch_emits_update_with_phase():
    updates: list = []
    elec = ElectricalHandler(
        local_client_feature=spine_addr(device="d:_i:1_me", entity=1, feature=5),
        on_update=lambda u: None,
    )
    elec.handle(
        "electricalConnectionParameterDescriptionListData",
        _reply((1, 1), 5),
        {
            "electricalConnectionParameterDescriptionListData": [
                {
                    "measurementId": 1,
                    "parameterId": 10,
                    "acMeasuredPhases": "a",
                    "acMeasurementType": "real",
                }
            ]
        },
    )
    meas = _measurement(electrical=elec, on_update=updates.append)
    meas.handle(
        "measurementDescriptionListData",
        _reply((1, 1), 1),
        {
            "measurementDescriptionListData": [
                {"measurementId": 1, "scopeType": "ACPower", "unit": "W", "measurementType": "real"}
            ]
        },
    )
    assert meas.description_map[((1, 1), 1)] == {
        1: {"scopeType": "ACPower", "unit": "W", "measurementType": "real"}
    }

    meas.handle(
        "measurementListData",
        _notify((1, 1), 1),
        {
            "measurementListData": [
                {"measurementId": 1, "measurementData": {"value": {"number": 90, "scale": -1}}}
            ]
        },
    )
    assert len(updates) == 1
    u = updates[0]
    assert isinstance(u, MeasurementUpdate)
    assert u.key == "acpower_e1_1_f1_id1"  # stable slug — becomes the HA entity id
    assert u.value == 9.0
    assert u.unit == "W"
    assert u.scope_type == "ACPower"
    assert u.measurement_id == 1
    assert u.source_entity == (1, 1)
    assert u.source_feature == 1
    assert u.phase is not None and u.phase["phases"] == "a"


def test_measurement_without_electrical_has_no_phase():
    updates: list = []
    meas = _measurement(on_update=updates.append)  # electrical handler has no params
    meas.handle(
        "measurementDescriptionListData",
        _reply((1, 1), 1),
        {
            "measurementDescriptionListData": [
                {"measurementId": 1, "scopeType": "DHWTemperature", "unit": "degC"}
            ]
        },
    )
    meas.handle(
        "measurementListData",
        _notify((1, 1), 1),
        {
            "measurementListData": [
                {"measurementId": 1, "measurementData": {"value": {"number": 660, "scale": -1}}}
            ]
        },
    )
    assert updates[0].value == 66.0
    assert updates[0].unit == "°C"  # degC normalized
    assert updates[0].phase is None


def test_setpoint_dispatch_emits_update():
    updates: list = []
    sp = _setpoint(on_update=updates.append)
    sp.handle(
        "setpointDescriptionListData",
        _reply((1, 1), 2),
        {
            "setpointDescriptionListData": [
                {
                    "setpointId": 1,
                    "setpointType": "dhwTemperature",
                    "scopeType": "dhwTemperature",
                    "unit": "degC",
                }
            ]
        },
    )
    sp.handle(
        "setpointListData",
        _notify((1, 1), 2),
        {"setpointListData": [{"setpointId": 1, "value": {"number": 480, "scale": -1}}]},
    )
    assert len(updates) == 1
    u = updates[0]
    assert isinstance(u, SetpointUpdate)
    assert u.key == "setpoint_dhwtemperature_e1_1_f2_id1"
    assert u.value == 48.0
    assert u.unit == "°C"
    assert u.setpoint_id == 1
    assert u.source_entity == (1, 1)


def test_setpoint_captures_constraints_without_gating_readiness():
    sp = _setpoint()
    sp._servers = [SpineAddress((1, 1), 2)]
    sp._pending_descriptions = 1  # the one gating description key
    # The auxiliary constraints reply parses and is stored, but must NOT count
    # down readiness (it isn't a description_cmd_key).
    sp.handle(
        "setpointConstraintsListData",
        _reply((1, 1), 2),
        {
            "setpointConstraintsListData": [
                {
                    "setpointId": 1,
                    "setpointRangeMin": {"number": 35, "scale": 0},
                    "setpointRangeMax": {"number": 7, "scale": 1},
                    "setpointStepSize": {"number": 1, "scale": 0},
                }
            ]
        },
    )
    assert sp.constraint_map[((1, 1), 2)] == {
        1: {"rangeMin": 35.0, "rangeMax": 70.0, "stepSize": 1.0}
    }
    assert not sp.descriptions_ready.is_set()  # auxiliary reply didn't gate
    assert "setpointConstraintsListData" in sp.auxiliary_cmd_keys
    assert "setpointConstraintsListData" not in sp.description_cmd_keys


def test_hvac_dispatch_emits_update():
    updates: list = []
    hv = _hvac(on_update=updates.append)
    hv.handle(
        "hvacOperationModeDescriptionListData",
        _reply((1, 1), 3),
        {
            "hvacOperationModeDescriptionListData": [
                {"operationModeId": 2, "operationModeType": "auto"}
            ]
        },
    )
    hv.handle(
        "hvacSystemFunctionDescriptionListData",
        _reply((1, 1), 3),
        {
            "hvacSystemFunctionDescriptionListData": [
                {"systemFunctionId": 1, "systemFunctionType": "dhw"}
            ]
        },
    )
    hv.handle(
        "hvacSystemFunctionListData",
        _notify((1, 1), 3),
        {"hvacSystemFunctionListData": [{"systemFunctionId": 1, "currentOperationModeId": 2}]},
    )
    assert len(updates) == 1
    u = updates[0]
    assert isinstance(u, HvacModeUpdate)
    assert u.key == "hvacmode_e1_1_f3_sf1"
    assert u.value == "auto"  # current op mode resolved against the description map
    assert u.mode == "auto"
    assert u.system_function_id == 1
    assert u.system_function_type == "dhw"


def test_hvac_mode_update_carries_flags():
    updates: list = []
    hv = _hvac(on_update=updates.append)
    hv.handle(
        "hvacOperationModeDescriptionListData",
        _reply((4,), 9),
        {
            "hvacOperationModeDescriptionListData": [
                {"operationModeId": 0, "operationModeType": "auto"}
            ]
        },
    )
    hv.handle(
        "hvacSystemFunctionListData",
        _notify((4,), 9),
        {
            "hvacSystemFunctionListData": [
                {
                    "systemFunctionId": 0,
                    "currentOperationModeId": 0,
                    "isOperationModeIdChangeable": True,
                    "isOverrunActive": False,
                }
            ]
        },
    )
    (u,) = updates
    assert isinstance(u, HvacModeUpdate)
    assert u.mode_changeable is True
    assert u.overrun_active is False


def test_hvac_overrun_dispatch_emits_update_without_gating_readiness():
    updates: list = []
    hv = _hvac(on_update=updates.append)
    hv._servers = [SpineAddress((4,), 9)]
    hv._pending_descriptions = 2  # the two gating description keys
    # Description first so the emitted update is enriched with type + affected sf.
    hv.handle(
        "hvacOverrunDescriptionListData",
        _reply((4,), 9),
        {
            "hvacOverrunDescriptionListData": [
                {"overrunId": 0, "overrunType": "oneTimeDhw", "affectedSystemFunctionId": [0]}
            ]
        },
    )
    hv.handle(
        "hvacOverrunListData",
        _notify((4,), 9),
        {"hvacOverrunListData": [{"overrunId": 0, "overrunStatus": "active"}]},
    )
    (u,) = updates
    assert isinstance(u, HvacOverrunUpdate)
    assert u.key == "hvacoverrun_e4_f9_ov0"
    assert u.active is True
    assert u.overrun_type == "oneTimeDhw"
    assert u.affected_system_function_ids == (0,)
    assert u.source_entity == (4,)
    # Overrun replies are auxiliary — they must not gate the descriptions step.
    assert not hv.descriptions_ready.is_set()
    assert "hvacOverrunDescriptionListData" in hv.auxiliary_cmd_keys
    assert "hvacOverrunListData" in hv.auxiliary_cmd_keys


def test_hvac_build_overrun_write_frame():
    hv = _hvac()
    hv._overrun_desc_maps[((4,), 9)] = {0: {"overrunType": "oneTimeDhw"}}
    hv._servers = [SpineAddress((4,), 9)]
    frame = hv.build_overrun_write(entity_tuple=(4,), feature=9, overrun_id=0, active=True)
    assert frame.address_destination == SpineAddress((4,), 9)
    assert frame.cmd == {
        "hvacOverrunListData": {"hvacOverrunData": [{"overrunId": 0, "overrunStatus": "active"}]}
    }
    off = hv.build_overrun_write(entity_tuple=(4,), feature=9, overrun_id=0, active=False)
    assert off.cmd["hvacOverrunListData"]["hvacOverrunData"][0]["overrunStatus"] == "inactive"


def test_hvac_build_overrun_write_validation():
    hv = _hvac()
    hv._overrun_desc_maps[((4,), 9)] = {0: {}}
    with pytest.raises(ValueError, match="Unknown overrunId=9"):
        hv.build_overrun_write(entity_tuple=(4,), feature=9, overrun_id=9, active=True)
    # id known but no server matches
    with pytest.raises(ValueError, match="No HVAC server matches"):
        hv.build_overrun_write(entity_tuple=(4,), feature=9, overrun_id=0, active=True)


def test_hvac_captures_setpoint_relations_without_gating_readiness():
    hv = _hvac()
    hv._servers = [SpineAddress((5, 1, 1), 9)]
    hv._pending_descriptions = 2  # the two gating description keys (opmode + sysfunc)
    # The auxiliary relation reply parses and is stored, but must NOT count down
    # readiness (it isn't a description_cmd_key).
    hv.handle(
        "hvacSystemFunctionSetpointRelationListData",
        _reply((5, 1, 1), 9),
        {
            "hvacSystemFunctionSetpointRelationListData": [
                {"systemFunctionId": 0, "operationModeId": 1, "setpointId": [1]},
                {"systemFunctionId": 0, "operationModeId": 3, "setpointId": [3]},
            ]
        },
    )
    assert hv.setpoint_relation_map[((5, 1, 1), 9)] == {0: {1: [1], 3: [3]}}
    assert not hv.descriptions_ready.is_set()  # auxiliary reply didn't gate
    assert "hvacSystemFunctionSetpointRelationListData" in hv.auxiliary_cmd_keys
    assert "hvacSystemFunctionSetpointRelationListData" not in hv.description_cmd_keys


def test_device_classification_dispatch_collects_manufacturer_and_user():
    dc = _device_classification()
    dc.handle(
        "deviceClassificationManufacturerData",
        _reply((3,), 4),
        {
            "deviceClassificationManufacturerData": {
                "deviceName": "Example Heat Pump",
                "deviceCode": "EX-100",
                "serialNumber": "0000000001",
                "brandName": "Example",
            }
        },
    )
    dc.handle(
        "deviceClassificationUserData",
        _reply((5, 1), 4),
        {"deviceClassificationUserData": {"userLabel": "Zone 1"}},
    )
    cmap = dc.classification_map
    assert cmap[(3,)]["manufacturer"]["serialNumber"] == "0000000001"
    assert cmap[(3,)]["manufacturer"]["deviceName"] == "Example Heat Pump"
    assert cmap[(5, 1)]["user"]["userLabel"] == "Zone 1"


# --------------------------------------------------------------------------
# bring-up: set_servers_from_discovery + handle() readiness latching
# --------------------------------------------------------------------------


def test_set_servers_latches_per_feature(discovery):
    elec = ElectricalHandler(
        local_client_feature=spine_addr(device="d:_i:1_me", entity=1, feature=5),
        on_update=lambda u: None,
    )
    elec.set_servers_from_discovery(discovery)
    assert len(elec.servers) == 1
    # ElectricalConnection has no value_cmd_keys → values are trivially "ready",
    # but it does have descriptions to fetch, so descriptions stay pending.
    assert elec.values_ready.is_set()
    assert not elec.descriptions_ready.is_set()

    meas = _measurement()
    meas.set_servers_from_discovery(discovery)
    assert len(meas.servers) == 1
    assert not meas.descriptions_ready.is_set()
    assert not meas.values_ready.is_set()


def test_set_servers_no_match_latches_both():
    sp = _setpoint()
    sp.set_servers_from_discovery({})  # no featureInformation → no Setpoint server
    assert sp.servers == []
    assert sp.descriptions_ready.is_set()
    assert sp.values_ready.is_set()


def test_descriptions_ready_counts_down_replies():
    sp = _setpoint()
    sp._servers = [SpineAddress((1, 1), 2)]
    sp._pending_descriptions = 1  # as request_descriptions would set (1 server × 1 key)
    assert not sp.descriptions_ready.is_set()
    sp.handle(
        "setpointDescriptionListData",
        _reply((1, 1), 2),
        {
            "setpointDescriptionListData": [
                {"setpointId": 1, "setpointType": "x", "scopeType": "y", "unit": "degC"}
            ]
        },
    )
    assert sp.descriptions_ready.is_set()


def test_hvac_descriptions_need_both_replies():
    hv = _hvac()
    hv._servers = [SpineAddress((1, 1), 3)]
    hv._pending_descriptions = 2  # 1 server × 2 description keys
    hdr = _reply((1, 1), 3)
    hv.handle(
        "hvacOperationModeDescriptionListData",
        hdr,
        {
            "hvacOperationModeDescriptionListData": [
                {"operationModeId": 1, "operationModeType": "off"}
            ]
        },
    )
    assert not hv.descriptions_ready.is_set()  # only one of the two landed
    hv.handle(
        "hvacSystemFunctionDescriptionListData",
        hdr,
        {
            "hvacSystemFunctionDescriptionListData": [
                {"systemFunctionId": 1, "systemFunctionType": "dhw"}
            ]
        },
    )
    assert hv.descriptions_ready.is_set()


def _dc_discovery():
    """Discovery with two DeviceClassification servers, each advertising just one
    function: appliance entity ``[3]`` → ManufacturerData, zone ``[5, 1]`` →
    UserData (the real device's split — see .scratchpad detailed discovery)."""
    return {
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
                    "supportedFunction": [{"function": "deviceClassificationUserData"}],
                }
            },
        ]
    }


def test_device_classification_reads_only_advertised_function():
    # Each DeviceClassification server implements only ONE of the two functions.
    # Reading both earns a rejected `result` for the unsupported half on every
    # server; we consult `supportedFunction` and read just what is offered.
    dc = _device_classification()
    dc.set_servers_from_discovery(_dc_discovery())
    reads = dict(dc._descriptions_to_read())
    assert reads[SpineAddress((3,), 4)] == ["deviceClassificationManufacturerData"]
    assert reads[SpineAddress((5, 1), 4)] == ["deviceClassificationUserData"]
    # Exactly one read per server → the readiness counter lands on zero naturally.
    assert sum(len(v) for v in reads.values()) == 2


def test_device_classification_unadvertised_server_falls_back_to_both():
    # A server that omits supportedFunction keeps the old best-effort behaviour:
    # read both functions rather than guessing.
    dc = _device_classification()
    dc.set_servers_from_discovery(
        {
            "featureInformation": [
                {
                    "description": {
                        "featureAddress": {"entity": [3], "feature": 4},
                        "featureType": "DeviceClassification",
                        "role": "server",
                    }
                }
            ]
        }
    )
    ((_server, names),) = dc._descriptions_to_read()
    assert names == [
        "deviceClassificationManufacturerData",
        "deviceClassificationUserData",
    ]


def test_device_classification_readiness_counts_one_reply_per_server():
    # With precise reads (one advertised function per server), the base counter is
    # the real one: 2 servers × 1 read = 2, and one reply per server completes it.
    dc = _device_classification()
    dc._servers = [SpineAddress((3,), 4), SpineAddress((5, 1), 4)]
    dc._pending_descriptions = 2  # what request_descriptions now sets
    dc.handle(
        "deviceClassificationManufacturerData",
        _reply((3,), 4),
        {"deviceClassificationManufacturerData": {"serialNumber": "0000000001"}},
    )
    assert not dc.descriptions_ready.is_set()  # zone (5,1) hasn't answered yet
    dc.handle(
        "deviceClassificationUserData",
        _reply((5, 1), 4),
        {"deviceClassificationUserData": {"userLabel": "Zone 1"}},
    )
    assert dc.descriptions_ready.is_set()
    assert dc.classification_map[(3,)]["manufacturer"]["serialNumber"] == "0000000001"
    assert dc.classification_map[(5, 1)]["user"]["userLabel"] == "Zone 1"


def test_notify_does_not_count_toward_readiness():
    sp = _setpoint()
    sp._servers = [SpineAddress((1, 1), 2)]
    sp._pending_descriptions = 1
    sp.handle(
        "setpointDescriptionListData",
        _notify((1, 1), 2),  # notify, not reply → must not count down
        {
            "setpointDescriptionListData": [
                {"setpointId": 1, "setpointType": "x", "scopeType": "y", "unit": "degC"}
            ]
        },
    )
    assert not sp.descriptions_ready.is_set()


def test_values_ready_requires_requested_flag():
    sp = _setpoint()
    sp._servers = [SpineAddress((1, 1), 2)]
    sp._pending_values = 1
    value_reply = (
        "setpointListData",
        _reply((1, 1), 2),
        {"setpointListData": [{"setpointId": 1, "value": {"number": 480, "scale": -1}}]},
    )
    # Until request_values has run (which sets _values_requested), a stray value
    # reply must not satisfy values_ready.
    sp.handle(*value_reply)
    assert not sp.values_ready.is_set()
    sp._values_requested = True
    sp.handle(*value_reply)
    assert sp.values_ready.is_set()


# --------------------------------------------------------------------------
# write-frame builders (pure: validate + resolve server + build frame)
# --------------------------------------------------------------------------


def test_setpoint_build_write_frame():
    sp = _setpoint()
    sp._desc_maps[((1, 1), 2)] = {1: {"setpointType": "dhwTemperature"}}
    sp._servers = [SpineAddress((1, 1), 2)]
    frame = sp.build_write(entity_tuple=(1, 1), feature=2, setpoint_id=1, value=48.0)
    assert frame.address_source == SpineAddress((1,), 4, "d:_i:1_me")
    # device-less destination — the session injects the connected device at send time
    assert frame.address_destination == SpineAddress((1, 1), 2)
    assert frame.cmd == {
        "setpointListData": {
            "setpointData": [{"setpointId": 1, "value": {"number": 480, "scale": -1}}]
        }
    }


def test_setpoint_build_write_validation():
    sp = _setpoint()
    # no descriptions cached for the target feature
    with pytest.raises(ValueError, match="No setpoint descriptions"):
        sp.build_write(entity_tuple=(1, 1), feature=2, setpoint_id=1, value=1.0)
    # descriptions known but the id is not among them
    sp._desc_maps[((1, 1), 2)] = {1: {}}
    with pytest.raises(ValueError, match="Unknown setpointId=99"):
        sp.build_write(entity_tuple=(1, 1), feature=2, setpoint_id=99, value=1.0)
    # id known but no server matches (e.g. discovery hadn't populated it)
    with pytest.raises(ValueError, match="No Setpoint server matches"):
        sp.build_write(entity_tuple=(1, 1), feature=2, setpoint_id=1, value=1.0)


def test_hvac_build_write_frame():
    hv = _hvac()
    hv._sys_func_maps[((1, 1), 3)] = {1: {"systemFunctionType": "dhw"}}
    hv._op_mode_maps[((1, 1), 3)] = {1: "off", 2: "auto"}
    hv._servers = [SpineAddress((1, 1), 3)]
    frame = hv.build_write(
        entity_tuple=(1, 1),
        feature=3,
        system_function_id=1,
        operation_mode_id=2,
    )
    assert frame.address_source == SpineAddress((1,), 3, "d:_i:1_me")
    # device-less destination — the session injects the connected device at send time
    assert frame.address_destination == SpineAddress((1, 1), 3)
    assert frame.cmd == {
        "hvacSystemFunctionListData": {
            "hvacSystemFunctionData": [{"systemFunctionId": 1, "currentOperationModeId": 2}]
        }
    }


def test_hvac_build_write_validation():
    hv = _hvac()
    hv._sys_func_maps[((1, 1), 3)] = {1: {"systemFunctionType": "dhw"}}
    hv._op_mode_maps[((1, 1), 3)] = {2: "auto"}
    with pytest.raises(ValueError, match="Unknown systemFunctionId=9"):
        hv.build_write(
            entity_tuple=(1, 1),
            feature=3,
            system_function_id=9,
            operation_mode_id=2,
        )
    with pytest.raises(ValueError, match="Unknown operationModeId=9"):
        hv.build_write(
            entity_tuple=(1, 1),
            feature=3,
            system_function_id=1,
            operation_mode_id=9,
        )
    hv._servers = []
    with pytest.raises(ValueError, match="No HVAC server matches"):
        hv.build_write(
            entity_tuple=(1, 1),
            feature=3,
            system_function_id=1,
            operation_mode_id=2,
        )


def test_hvac_resolve_operation_mode():
    hv = _hvac()
    hv._op_mode_maps[((1, 1), 3)] = {1: "off", 2: "auto", 3: "heating"}
    assert hv.resolve_operation_mode((1, 1), 3, 2) == 2  # int passthrough
    assert hv.resolve_operation_mode((1, 1), 3, "auto") == 2  # by name
    assert hv.resolve_operation_mode((1, 1), 3, "HEATING") == 3  # case-insensitive
    with pytest.raises(ValueError, match="Unknown operationModeId=9"):
        hv.resolve_operation_mode((1, 1), 3, 9)
    with pytest.raises(ValueError, match="Unknown operation mode"):
        hv.resolve_operation_mode((1, 1), 3, "nope")
    with pytest.raises(ValueError, match="not bool"):
        hv.resolve_operation_mode((1, 1), 3, True)


def test_hvac_resolve_operation_mode_no_modes_known():
    hv = _hvac()
    with pytest.raises(ValueError, match="No HVAC operation modes"):
        hv.resolve_operation_mode((1, 1), 3, 1)
