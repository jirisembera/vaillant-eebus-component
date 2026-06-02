"""Tests for the Measurement parsers and the shared scaledNumber helpers."""

from __future__ import annotations

from vaillant_eebus.parsers import (
    coerce_list,
    float_to_scaled_number,
    parse_measurement_description,
    parse_measurement_list,
    scaled_number_to_float,
)
from vaillant_eebus.spine import SpineAddress

# ── _common helpers ─────────────────────────────────────────────────────────


def test_scaled_number_to_float_basic():
    assert scaled_number_to_float({"number": 660, "scale": -1}) == 66.0
    assert scaled_number_to_float({"number": 9, "scale": 0}) == 9.0
    assert scaled_number_to_float({"number": 5}) == 5.0  # default scale 0


def test_scaled_number_to_float_rejects_bad_input():
    assert scaled_number_to_float(None) is None
    assert scaled_number_to_float("x") is None
    assert scaled_number_to_float({"scale": -1}) is None  # no number
    assert scaled_number_to_float({"number": "x"}) is None


def test_scaled_number_non_int_scale_defaults_zero():
    assert scaled_number_to_float({"number": 5, "scale": "bad"}) == 5.0


def test_float_to_scaled_number_roundtrip():
    enc = float_to_scaled_number(48.0)
    assert enc == {"number": 480, "scale": -1}
    assert scaled_number_to_float(enc) == 48.0


def test_coerce_list_variants():
    assert coerce_list({"k": [1, 2]}, "k") == [1, 2]
    assert coerce_list({"k": {"k": [3]}}, "k") == [3]
    assert coerce_list({"k": {"alt": [4]}}, "k", "alt") == [4]
    assert coerce_list({"k": 7}, "k") is None
    assert coerce_list({}, "k") is None


# ── parse_measurement_description ────────────────────────────────────────────


def test_parse_measurement_description():
    cmd = {
        "measurementDescriptionListData": [
            {
                "measurementId": 1,
                "scopeType": "DHWTemperature",
                "unit": "degC",
                "measurementType": "temperature",
            },
            {"measurementId": 2, "scopeType": "ACPowerTotal", "unit": "W"},
            {"scopeType": "missing-id"},  # skipped: no int id
        ]
    }
    desc = parse_measurement_description(cmd)
    assert set(desc) == {1, 2}
    assert desc[1] == {
        "scopeType": "DHWTemperature",
        "unit": "degC",
        "measurementType": "temperature",
    }
    assert desc[2]["measurementType"] is None


def test_parse_measurement_description_wrapped():
    cmd = {
        "measurementDescriptionListData": {
            "measurementDescriptionData": [
                {"measurementId": 7, "scopeType": "Outside", "unit": "degC"}
            ]
        }
    }
    desc = parse_measurement_description(cmd)
    assert desc[7]["scopeType"] == "Outside"


def test_parse_measurement_description_bad_type_returns_empty():
    assert parse_measurement_description({"measurementDescriptionListData": 5}) == {}


# ── parse_measurement_list ───────────────────────────────────────────────────


def test_parse_measurement_list_canonical():
    desc = {1: {"scopeType": "DHWTemperature", "unit": "degC", "measurementType": "temperature"}}
    cmd = {
        "measurementListData": [
            {"measurementId": 1, "measurementData": {"value": {"number": 660, "scale": -1}}},
        ]
    }
    src = {"entity": [1, 1], "feature": 1}
    out = parse_measurement_list(cmd, desc, source_address=src)
    assert len(out) == 1
    row = out[0]
    assert row.measurement_id == 1
    assert row.value == 66.0
    assert row.scope_type == "DHWTemperature"
    assert row.unit == "degC"
    assert row.source == SpineAddress((1, 1), 1)


def test_parse_measurement_list_inline_value_fallback():
    cmd = {"measurementListData": [{"measurementId": 2, "value": {"number": 9, "scale": 0}}]}
    out = parse_measurement_list(cmd, {}, source_address=None)
    assert out[0].value == 9.0
    assert out[0].scope_type == "unknown"
    assert out[0].source == SpineAddress()


def test_parse_measurement_list_skips_valueless_and_nondict():
    cmd = {
        "measurementListData": [
            {"measurementId": 3},  # no value
            {"measurementId": 4, "measurementData": {}},  # empty data
            "notadict",
        ]
    }
    assert parse_measurement_list(cmd, {}) == []


def test_parse_measurement_list_unit_dict_coerced_to_string():
    desc = {1: {"scopeType": "X", "unit": {"unit": "degC"}, "measurementType": "t"}}
    cmd = {
        "measurementListData": [
            {"measurementId": 1, "measurementData": {"value": {"number": 10, "scale": 0}}}
        ]
    }
    out = parse_measurement_list(cmd, desc)
    assert out[0].unit == "degC"
