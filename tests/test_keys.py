"""Tests for the stable update-key formulas (vaillant_eebus.keys).

These keys are the single source of truth shared by the live handlers (which
stamp them on emitted Updates) and build_topology (which precomputes them on
description nodes). The exact slugs asserted here must match the ones the
handler tests expect (see tests/test_handlers.py), so the two paths stay locked
together.
"""

from __future__ import annotations

from vaillant_eebus.keys import (
    hvac_mode_key,
    hvac_overrun_key,
    measurement_key,
    setpoint_key,
)


def test_measurement_key_matches_handler_slug():
    assert (
        measurement_key(scope_type="ACPower", entity=(1, 1), feature=1, measurement_id=1)
        == "acpower_e1_1_f1_id1"
    )


def test_measurement_key_scope_fallback():
    # Missing scope mirrors the parser's "unknown" default.
    assert (
        measurement_key(scope_type=None, entity=(4,), feature=11, measurement_id=9)
        == "unknown_e4_f11_id9"
    )


def test_measurement_key_empty_entity_is_na():
    assert measurement_key(scope_type="x", entity=(), feature=1, measurement_id=0) == "x_ena_f1_id0"


def test_setpoint_key_matches_handler_slug():
    assert (
        setpoint_key(
            scope_type="dhwTemperature",
            setpoint_type="dhwTemperature",
            entity=(1, 1),
            feature=2,
            setpoint_id=1,
        )
        == "setpoint_dhwtemperature_e1_1_f2_id1"
    )


def test_setpoint_key_falls_back_scope_then_type_then_default():
    # scopeType wins when present.
    assert (
        setpoint_key(
            scope_type="roomAir",
            setpoint_type="valueAbsolute",
            entity=(5,),
            feature=2,
            setpoint_id=3,
        )
        == "setpoint_roomair_e5_f2_id3"
    )
    # else setpointType.
    assert (
        setpoint_key(
            scope_type=None, setpoint_type="valueAbsolute", entity=(5,), feature=2, setpoint_id=3
        )
        == "setpoint_valueabsolute_e5_f2_id3"
    )
    # else the literal "setpoint".
    assert (
        setpoint_key(scope_type=None, setpoint_type=None, entity=(5,), feature=2, setpoint_id=3)
        == "setpoint_setpoint_e5_f2_id3"
    )


def test_hvac_mode_key_matches_handler_slug():
    assert hvac_mode_key(entity=(1, 1), feature=3, system_function_id=1) == "hvacmode_e1_1_f3_sf1"


def test_hvac_overrun_key_matches_handler_slug():
    assert hvac_overrun_key(entity=(4,), feature=9, overrun_id=0) == "hvacoverrun_e4_f9_ov0"
