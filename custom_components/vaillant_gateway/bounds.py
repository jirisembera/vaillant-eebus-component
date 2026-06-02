"""Resolve a writable setpoint's HA bounds, preferring the device's own limits.

The gateway reports per-setpoint ``setpointConstraintsListData`` (min / max /
step), surfaced on :class:`SetpointDescription`. When a limit is present we use
it; otherwise we fall back to the scope/entity-type guesses the entities have
always carried (so a gateway that omits constraints keeps working unchanged).
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from .vaillant_eebus import SetpointDescription


class Bounds(NamedTuple):
    """A writable temperature range: ``min``/``max`` limits and the step size."""

    min: float
    max: float
    step: float


# Last-resort ranges used when the gateway reports no per-setpoint constraints.
DHW_FALLBACK = Bounds(30.0, 70.0, 0.5)
ROOM_FALLBACK = Bounds(5.0, 30.0, 0.5)


def setpoint_bounds(desc: Optional[SetpointDescription], fallback: Bounds) -> Bounds:
    """``Bounds`` taken from ``desc`` where the device reported it, else ``fallback``."""
    return Bounds(
        min=desc.range_min if desc and desc.range_min is not None else fallback.min,
        max=desc.range_max if desc and desc.range_max is not None else fallback.max,
        step=desc.step if desc and desc.step is not None else fallback.step,
    )
