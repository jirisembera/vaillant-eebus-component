"""Tests for the HVAC feature parsers."""

from __future__ import annotations

from vaillant_eebus.parsers import (
    ParsedOverrun,
    ParsedSystemFunction,
    parse_hvac_operation_mode_descriptions,
    parse_hvac_overrun_descriptions,
    parse_hvac_overrun_list,
    parse_hvac_system_function_descriptions,
    parse_hvac_system_function_list,
    parse_hvac_system_function_setpoint_relations,
)
from vaillant_eebus.spine import SpineAddress


def test_operation_mode_descriptions():
    cmd = {
        "hvacOperationModeDescriptionListData": [
            {"operationModeId": 1, "operationModeType": "auto"},
            {"operationModeId": 2, "operationModeType": "off"},
            {"operationModeId": 3},  # no type → skipped
            {"operationModeId": "x", "operationModeType": "bad"},  # non-int id → skipped
        ]
    }
    assert parse_hvac_operation_mode_descriptions(cmd) == {1: "auto", 2: "off"}


def test_operation_mode_descriptions_wrapped_and_empty():
    assert parse_hvac_operation_mode_descriptions({}) == {}
    wrapped = {
        "hvacOperationModeDescriptionListData": {
            "hvacOperationModeDescriptionData": [{"operationModeId": 5, "operationModeType": "eco"}]
        }
    }
    assert parse_hvac_operation_mode_descriptions(wrapped) == {5: "eco"}


def test_system_function_descriptions():
    cmd = {
        "hvacSystemFunctionDescriptionListData": [
            {
                "systemFunctionId": 1,
                "systemFunctionType": "dhwTemperature",
                "operationModeIds": [1, 2],
                "description": "DHW",
            },
            {"systemFunctionType": "missing-id"},  # skipped
        ]
    }
    out = parse_hvac_system_function_descriptions(cmd)
    assert set(out) == {1}
    assert out[1]["systemFunctionType"] == "dhwTemperature"
    assert out[1]["operationModeIds"] == [1, 2]


def test_system_function_list_with_source():
    cmd = {
        "hvacSystemFunctionListData": [
            {"systemFunctionId": 1, "currentOperationModeId": 2},
            {"systemFunctionId": 2},  # no current → None
            {"currentOperationModeId": 9},  # no id → skipped
        ]
    }
    src = {"entity": [1, 1], "feature": 3}
    out = parse_hvac_system_function_list(cmd, source_address=src)
    assert out == [
        ParsedSystemFunction(
            system_function_id=1,
            source=SpineAddress((1, 1), 3),
            current_operation_mode_id=2,
        ),
        ParsedSystemFunction(
            system_function_id=2,
            source=SpineAddress((1, 1), 3),
            current_operation_mode_id=None,
        ),
    ]


def test_system_function_list_no_source():
    cmd = {"hvacSystemFunctionListData": [{"systemFunctionId": 1, "currentOperationModeId": 1}]}
    out = parse_hvac_system_function_list(cmd)
    assert out[0].source == SpineAddress()


def test_system_function_list_carries_flags():
    # The DHW row carries the changeable + overrun-active flags alongside the mode.
    cmd = {
        "hvacSystemFunctionListData": [
            {
                "systemFunctionId": 0,
                "currentOperationModeId": 0,
                "isOperationModeIdChangeable": True,
                "isOverrunActive": False,
            },
            {"systemFunctionId": 1, "currentOperationModeId": 1},  # flags omitted → None
        ]
    }
    out = parse_hvac_system_function_list(cmd)
    assert out[0].is_operation_mode_changeable is True
    assert out[0].is_overrun_active is False
    assert out[1].is_operation_mode_changeable is None
    assert out[1].is_overrun_active is None


def test_overrun_descriptions():
    cmd = {
        "hvacOverrunDescriptionListData": [
            {"overrunId": 0, "overrunType": "oneTimeDhw", "affectedSystemFunctionId": [0]},
            {"overrunType": "no-id"},  # skipped
        ]
    }
    out = parse_hvac_overrun_descriptions(cmd)
    assert set(out) == {0}
    assert out[0]["overrunType"] == "oneTimeDhw"
    assert out[0]["affectedSystemFunctionId"] == [0]


def test_overrun_descriptions_scalar_affected_and_wrapped():
    wrapped = {
        "hvacOverrunDescriptionListData": {
            "hvacOverrunDescriptionData": [
                {"overrunId": 2, "overrunType": "x", "affectedSystemFunctionId": 5},
            ]
        }
    }
    out = parse_hvac_overrun_descriptions(wrapped)
    assert out[2]["affectedSystemFunctionId"] == [5]
    assert parse_hvac_overrun_descriptions({}) == {}


def test_overrun_list():
    cmd = {
        "hvacOverrunListData": [
            {"overrunId": 0, "overrunStatus": "inactive"},
            {"overrunStatus": "active"},  # no id → skipped
        ]
    }
    src = {"entity": [4], "feature": 9}
    out = parse_hvac_overrun_list(cmd, source_address=src)
    assert out == [ParsedOverrun(overrun_id=0, source=SpineAddress((4,), 9), status="inactive")]


def test_setpoint_relations():
    # The HVACRoom shape from the real device: auto spans both setpoints, on→[1],
    # eco→[3], off→[] (omitted setpointId).
    cmd = {
        "hvacSystemFunctionSetpointRelationListData": [
            {"systemFunctionId": 0, "operationModeId": 0, "setpointId": [1, 3]},
            {"systemFunctionId": 0, "operationModeId": 1, "setpointId": [1]},
            {"systemFunctionId": 0, "operationModeId": 2},
            {"systemFunctionId": 0, "operationModeId": 3, "setpointId": [3]},
        ]
    }
    assert parse_hvac_system_function_setpoint_relations(cmd) == {
        0: {0: [1, 3], 1: [1], 2: [], 3: [3]}
    }


def test_setpoint_relations_scalar_and_wrapped():
    # A scalar setpointId is coerced to a single-element list; array-wrapped works.
    wrapped = {
        "hvacSystemFunctionSetpointRelationListData": {
            "hvacSystemFunctionSetpointRelationData": [
                {"systemFunctionId": 0, "operationModeId": 1, "setpointId": 2},
            ]
        }
    }
    assert parse_hvac_system_function_setpoint_relations(wrapped) == {0: {1: [2]}}
    assert parse_hvac_system_function_setpoint_relations({}) == {}
