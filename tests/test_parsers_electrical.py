"""Tests for the ElectricalConnection parameter-description parser."""

from __future__ import annotations

from vaillant_eebus.parsers import parse_electrical_param_descriptions


def test_electrical_param_descriptions():
    cmd = {
        "electricalConnectionParameterDescriptionListData": [
            {
                "measurementId": 10,
                "parameterId": 0,
                "acMeasuredPhases": "a",
                "acMeasurementType": "power",
                "acMeasurementVariant": "rms",
                "voltageType": "ac",
                "scopeType": "ACPower",
            },
            {"parameterId": 1},  # no measurementId → skipped
        ]
    }
    out = parse_electrical_param_descriptions(cmd)
    assert set(out) == {10}
    assert out[10]["phases"] == "a"
    assert out[10]["measurementType"] == "power"
    assert out[10]["variant"] == "rms"
    assert out[10]["parameterId"] == 0
    assert out[10]["voltageType"] == "ac"
    assert out[10]["scopeType"] == "ACPower"


def test_electrical_param_descriptions_wrapped():
    cmd = {
        "electricalConnectionParameterDescriptionListData": {
            "electricalConnectionParameterDescriptionData": [
                {"measurementId": 3, "acMeasuredPhases": "abc"}
            ]
        }
    }
    out = parse_electrical_param_descriptions(cmd)
    assert out[3]["phases"] == "abc"


def test_electrical_param_descriptions_empty():
    assert parse_electrical_param_descriptions({}) == {}
