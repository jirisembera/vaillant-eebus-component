"""Tests for the EEBUS array-wrapped JSON conversion helpers."""

from __future__ import annotations

import json

from vaillant_eebus.eebus_json import (
    json_from_eebus_json,
    json_into_eebus_json,
    json_text_into_eebus_json,
)

# ── forward: standard JSON → EEBUS array-wrapped (deterministic) ─────────────


def test_into_eebus_object_becomes_single_key_arrays():
    # Top-level array wrapper is stripped.
    assert json_into_eebus_json({"a": 1, "b": 2}) == '{"a":1},{"b":2}'


def test_into_eebus_nested_object():
    assert json_into_eebus_json({"outer": {"inner": 5}}) == '{"outer":[{"inner":5}]}'


def test_into_eebus_list_of_objects():
    assert (
        json_into_eebus_json({"items": [{"x": 1}, {"y": 2}]}) == '{"items":[[{"x":1}],[{"y":2}]]}'
    )


def test_text_into_eebus_preserves_field_order():
    assert json_text_into_eebus_json('{"b":1,"a":2}') == '{"b":1},{"a":2}'


# ── reverse: EEBUS array-wrapped → standard JSON (heuristic) ─────────────────


def test_from_eebus_simple_object():
    assert json.loads(json_from_eebus_json('[{"a":1}]')) == {"a": 1}


def test_from_eebus_merges_sibling_single_key_objects():
    assert json.loads(json_from_eebus_json('[{"a":1},{"b":2}]')) == {"a": 1, "b": 2}


def test_from_eebus_strips_trailing_nul_bytes():
    assert json_from_eebus_json('[{"a":1}]\x00\x00') == '{"a":1}'


def test_from_eebus_empty_array_becomes_empty_object():
    assert json_from_eebus_json("[]") == "{}"
