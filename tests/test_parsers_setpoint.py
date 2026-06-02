"""Tests for the Setpoint feature parsers."""

from __future__ import annotations

from vaillant_eebus.parsers import (
    parse_setpoint_constraints,
    parse_setpoint_descriptions,
    parse_setpoint_list,
)
from vaillant_eebus.spine import SpineAddress


def test_setpoint_descriptions():
    cmd = {
        "setpointDescriptionListData": [
            {
                "setpointId": 1,
                "setpointType": "dhwTemperature",
                "scopeType": "DHW",
                "unit": "degC",
                "description": "DHW",
            },
            {"scopeType": "no-id"},  # skipped
        ]
    }
    out = parse_setpoint_descriptions(cmd)
    assert set(out) == {1}
    assert out[1]["setpointType"] == "dhwTemperature"
    assert out[1]["unit"] == "degC"


def test_setpoint_list_value_path():
    desc = {1: {"setpointType": "dhw", "scopeType": "DHW", "unit": "degC"}}
    cmd = {"setpointListData": [{"setpointId": 1, "value": {"number": 500, "scale": -1}}]}
    out = parse_setpoint_list(cmd, desc, source_address={"entity": [4], "feature": 2})
    assert out[0].value == 50.0
    assert out[0].setpoint_type == "dhw"
    assert out[0].scope_type == "DHW"
    assert out[0].source == SpineAddress((4,), 2)


def test_setpoint_list_setpointvalue_fallback():
    # Some stacks put the value under setpointValue instead of value.
    cmd = {"setpointListData": [{"setpointId": 2, "setpointValue": {"number": 215, "scale": -1}}]}
    out = parse_setpoint_list(cmd, {})
    assert out[0].value == 21.5
    assert out[0].setpoint_type is None


def test_setpoint_list_skips_valueless_and_idless():
    cmd = {"setpointListData": [{"setpointId": 3}, {"value": {"number": 1, "scale": 0}}]}
    assert parse_setpoint_list(cmd, {}) == []


def test_setpoint_constraints():
    # Real DHW shape: positive scale on the max (7×10¹ = 70). An empty row (id 0)
    # decodes to all-None, and an id-less row is skipped.
    cmd = {
        "setpointConstraintsListData": [
            {"setpointId": 0},
            {
                "setpointId": 1,
                "setpointRangeMin": {"number": 35, "scale": 0},
                "setpointRangeMax": {"number": 7, "scale": 1},
                "setpointStepSize": {"number": 1, "scale": 0},
            },
            {"setpointRangeMin": {"number": 5, "scale": 0}},  # no id → skipped
        ]
    }
    out = parse_setpoint_constraints(cmd)
    assert set(out) == {0, 1}
    assert out[1] == {"rangeMin": 35.0, "rangeMax": 70.0, "stepSize": 1.0}
    assert out[0] == {"rangeMin": None, "rangeMax": None, "stepSize": None}


def test_setpoint_constraints_empty():
    assert parse_setpoint_constraints({}) == {}
