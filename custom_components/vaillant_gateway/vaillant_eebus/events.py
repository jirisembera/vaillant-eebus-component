"""Public update event types emitted by EebusClient.

Each kind of telemetry (Measurement, HVAC mode, Setpoint) is exposed as a
frozen dataclass with a stable ``key`` that identifies the underlying
sensor/setpoint across reconnects. Consumers (the CLI, the HA bridge,
user code) match on the dataclass type or on ``key``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple


@dataclass(frozen=True)
class Update:
    """Base update record.

    Attributes:
        key:        Stable slug identifier (suitable for MQTT topic / dict key).
        value:      Parsed value — typically float for measurements/setpoints,
                    str for textual modes.
        unit:       Normalized unit string ("°C", "W", "Hz", …) or "".
        timestamp:  Seconds since epoch when the update was received.
        source_entity:  SPINE entity address tuple of the originating feature.
        source_feature: SPINE feature id of the originating feature.
    """

    key: str
    value: Any
    unit: str
    timestamp: float
    source_entity: Tuple[int, ...]
    source_feature: int


@dataclass(frozen=True)
class MeasurementUpdate(Update):
    """A SPINE Measurement value (temperature, power, voltage, …)."""

    scope_type: str
    measurement_type: str
    measurement_id: int
    phase: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class HvacModeUpdate(Update):
    """Current operating mode of an HVAC system function."""

    system_function_id: int
    system_function_type: str
    mode: Optional[str] = None
    # Device-reported flags carried on the same row. ``mode_changeable`` is False
    # when the gateway currently forbids changing the mode; ``overrun_active``
    # mirrors whether an overrun (e.g. the one-time DHW boost) is running. Both
    # are None when the gateway omitted the flag.
    mode_changeable: Optional[bool] = None
    overrun_active: Optional[bool] = None

    @property
    def item_id(self) -> int:
        """The write-target item id for this update (its system-function id)."""
        return self.system_function_id


@dataclass(frozen=True)
class HvacOverrunUpdate(Update):
    """State of an HVAC overrun — e.g. the one-time DHW (hot-water) boost.

    ``active`` is the live on/off state; ``overrun_type`` (e.g. ``"oneTimeDhw"``)
    and ``affected_system_function_ids`` come from the overrun description.
    ``value`` carries the raw status string for human-readable consumers.
    """

    overrun_id: int
    active: bool
    overrun_type: str = ""
    affected_system_function_ids: Tuple[int, ...] = ()

    @property
    def item_id(self) -> int:
        """The write-target item id for this update (its overrun id)."""
        return self.overrun_id


@dataclass(frozen=True)
class SetpointUpdate(Update):
    """A SPINE Setpoint value (target temperature etc.)."""

    scope_type: str
    setpoint_type: str
    setpoint_id: int

    @property
    def item_id(self) -> int:
        """The write-target item id for this update (its setpoint id)."""
        return self.setpoint_id
