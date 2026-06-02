"""Tests for the friendly-name / unit / phase label helpers."""

from __future__ import annotations

import pytest
from vaillant_eebus.naming import (
    friendly_hvac_function_name,
    friendly_sensor_name,
    friendly_setpoint_name,
    measurement_emoji,
    phase_label,
    unit_to_ha,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("degC", "°C"),
        ("degF", "°F"),
        ("W", "W"),
        ("  W  ", "W"),
        ({"unit": "degC"}, "°C"),
        ({"name": "degF"}, "°F"),
        (None, ""),
        ({}, ""),
    ],
)
def test_unit_to_ha(raw, expected):
    assert unit_to_ha(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a", "L1"),
        ("b", "L2"),
        ("c", "L3"),
        ("abc", "Σ"),
        ("ab", "L1-L2"),
        ("xyz", "XYZ"),  # unknown → uppercased
        (None, ""),
    ],
)
def test_phase_label(raw, expected):
    assert phase_label(raw) == expected


def test_friendly_sensor_name_known_scopes():
    assert friendly_sensor_name("OutsideAirTemperature") == "Outdoor Temperature"
    assert friendly_sensor_name("DHWTemperature") == "DHW Temperature"
    assert friendly_sensor_name("RoomAirTemperature") == "Room Temperature"
    assert friendly_sensor_name("ACPowerTotal") == "Compressor Power Total"


def test_friendly_sensor_name_phase_suffix():
    # measurementType "real" is suppressed; phase becomes an L-label suffix.
    assert (
        friendly_sensor_name("ACPower", phase_info={"phases": "a", "measurementType": "real"})
        == "Compressor Power L1"
    )
    assert friendly_sensor_name("ACVoltage", phase_info={"phases": "ab"}) == "Voltage L1-L2"


def test_friendly_sensor_name_fallbacks():
    assert friendly_sensor_name("WeirdScope", source_entity=[2, 3]) == "WeirdScope (entity=[2, 3])"
    assert friendly_sensor_name("") == "Measurement"


def test_friendly_setpoint_name():
    assert friendly_setpoint_name("DHWsetpoint") == "DHW Setpoint"
    assert friendly_setpoint_name("flowTemp") == "Flow Temperature Setpoint"
    # No scope and no entity_type → generic label (no entity-number guessing).
    assert friendly_setpoint_name(None) == "Setpoint"
    assert friendly_setpoint_name("") == "Setpoint"


def test_friendly_setpoint_name_entity_type():
    # Device-provided entity type drives the classification (no numeric guess).
    assert friendly_setpoint_name(None, entity_type="DHWCircuit") == "DHW Setpoint"
    assert friendly_setpoint_name(None, entity_type="HVACRoom") == "Room Temperature Setpoint"
    assert friendly_setpoint_name(None, entity_type="HeatingZone") == "Room Temperature Setpoint"


def test_friendly_setpoint_name_mode_qualifier():
    # A distinctive mode (eco) adds a qualifier; scheduling modes (on/auto) don't.
    assert (
        friendly_setpoint_name("roomAirTemperature", mode_type="eco")
        == "Room Temperature Eco Setpoint"
    )
    assert (
        friendly_setpoint_name("roomAirTemperature", mode_type="on") == "Room Temperature Setpoint"
    )


def test_friendly_setpoint_name_user_label():
    # The zone label replaces the scope-derived noun; the eco qualifier still applies.
    assert (
        friendly_setpoint_name("roomAirTemperature", user_label="PODLAHOVKA", mode_type="eco")
        == "PODLAHOVKA Eco Setpoint"
    )
    assert (
        friendly_setpoint_name("roomAirTemperature", user_label="PODLAHOVKA")
        == "PODLAHOVKA Setpoint"
    )
    # Blank label falls back to the scope noun.
    assert (
        friendly_setpoint_name("roomAirTemperature", user_label="  ") == "Room Temperature Setpoint"
    )


def test_friendly_hvac_function_name():
    assert friendly_hvac_function_name("dhwLoading") == "DHW Mode"
    assert friendly_hvac_function_name("heatingCircuit") == "Heating Mode"
    assert friendly_hvac_function_name(None, system_function_id=7) == "Operating Mode (sf7)"
    assert friendly_hvac_function_name(None) == "Operating Mode"


def test_friendly_hvac_function_name_entity_type():
    assert friendly_hvac_function_name(None, entity_type="DHWCircuit") == "DHW Mode"
    assert friendly_hvac_function_name(None, entity_type="HVACRoom") == "Heating/Cooling Mode"
    assert friendly_hvac_function_name(None, entity_type="HeatingZone") == "Heating/Cooling Mode"


@pytest.mark.parametrize(
    "scope,emoji",
    [
        ("DHWTemperature", "🚿"),
        ("ACPowerTotal", "⚡"),
        ("ACVoltage", "🔌"),
        ("OutsideAirTemperature", "🌡️"),
        ("Mystery", "📊"),
    ],
)
def test_measurement_emoji(scope, emoji):
    assert measurement_emoji(scope) == emoji
