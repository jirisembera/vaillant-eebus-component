"""SPINE payload parsers.

Pure conversion from decoded SPINE ``cmd`` dicts to structured records. No
value-level logging happens here — the comm library exposes parsed values to
consumers via :mod:`vaillant_eebus.events`, and only those consumers decide whether/how
to log them.

Grouped by feature; each submodule is small and self-contained. Importers
should keep using ``from vaillant_eebus.parsers import ...`` — the re-exports below
preserve the flat surface.
"""

from __future__ import annotations

from ._common import (
    coerce_list,
    float_to_scaled_number,
    measurement_scope,
    scaled_number_to_float,
    setpoint_scope,
    str_or_none,
    unit_to_str,
)
from .device_classification import (
    parse_device_classification_manufacturer_data,
    parse_device_classification_user_data,
)
from .discovery import (
    entity_addr_list,
    extract_entities,
    extract_heat_pump_entity,
    extract_servers_by_type,
    extract_supported_functions_by_type,
)
from .electrical import parse_electrical_param_descriptions
from .hvac import (
    ParsedOverrun,
    ParsedSystemFunction,
    parse_hvac_operation_mode_descriptions,
    parse_hvac_overrun_descriptions,
    parse_hvac_overrun_list,
    parse_hvac_system_function_descriptions,
    parse_hvac_system_function_list,
    parse_hvac_system_function_setpoint_relations,
)
from .measurement import (
    ParsedMeasurement,
    parse_measurement_description,
    parse_measurement_list,
)
from .setpoint import (
    ParsedSetpoint,
    parse_setpoint_constraints,
    parse_setpoint_descriptions,
    parse_setpoint_list,
)

__all__ = [
    "coerce_list",
    "float_to_scaled_number",
    "measurement_scope",
    "scaled_number_to_float",
    "setpoint_scope",
    "str_or_none",
    "unit_to_str",
    "parse_device_classification_manufacturer_data",
    "parse_device_classification_user_data",
    "entity_addr_list",
    "extract_entities",
    "extract_heat_pump_entity",
    "extract_servers_by_type",
    "extract_supported_functions_by_type",
    "parse_electrical_param_descriptions",
    "ParsedMeasurement",
    "ParsedOverrun",
    "ParsedSetpoint",
    "ParsedSystemFunction",
    "parse_hvac_operation_mode_descriptions",
    "parse_hvac_overrun_descriptions",
    "parse_hvac_overrun_list",
    "parse_hvac_system_function_descriptions",
    "parse_hvac_system_function_list",
    "parse_hvac_system_function_setpoint_relations",
    "parse_measurement_description",
    "parse_measurement_list",
    "parse_setpoint_constraints",
    "parse_setpoint_descriptions",
    "parse_setpoint_list",
]
