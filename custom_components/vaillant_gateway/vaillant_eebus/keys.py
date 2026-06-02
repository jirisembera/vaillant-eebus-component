"""Stable update-key formulas — the single source of truth.

The slug that identifies a Measurement / Setpoint / HVAC item across reconnects
is derived purely from the item's description (scope / type) and its SPINE
address (entity + feature + item id). Two places compute it:

* the live handlers (:mod:`vaillant_eebus.handlers`), which stamp it onto every
  emitted :class:`vaillant_eebus.events.Update`, and
* :func:`vaillant_eebus.topology.build_topology`, which precomputes it onto each
  description node so consumers can navigate from a description straight to its
  current value (``desc.key in client.values``) without rescanning the update
  stream.

Both call these functions, so a description's ``key`` provably equals the key of
the :class:`Update` that description will emit. The scope fallbacks
(``scopeType or "unknown"`` for measurements, etc.) live once in
:mod:`vaillant_eebus.parsers._common` (``measurement_scope`` / ``setpoint_scope``)
and are shared with the parsers/handlers, so the key and the emitted value can't
drift apart.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .parsers import measurement_scope, setpoint_scope
from .utils import slug


def _entity_part(entity: Sequence[int]) -> str:
    return "_".join(str(x) for x in entity) if entity else "na"


def measurement_key(
    *,
    scope_type: Optional[str],
    entity: Sequence[int],
    feature: int,
    measurement_id: int,
) -> str:
    """Stable key for a Measurement value (mirrors the parser's scope fallback)."""
    scope = measurement_scope(scope_type)
    return slug(f"{scope}_e{_entity_part(entity)}_f{feature}_id{measurement_id}")


def setpoint_key(
    *,
    scope_type: Optional[str],
    setpoint_type: Optional[str],
    entity: Sequence[int],
    feature: int,
    setpoint_id: int,
) -> str:
    """Stable key for a Setpoint value (scopeType → setpointType → "setpoint")."""
    scope = setpoint_scope(scope_type, setpoint_type)
    return slug(f"setpoint_{scope}_e{_entity_part(entity)}_f{feature}_id{setpoint_id}")


def hvac_mode_key(
    *,
    entity: Sequence[int],
    feature: int,
    system_function_id: int,
) -> str:
    """Stable key for an HVAC operation-mode value (no scope component)."""
    return slug(f"hvacmode_e{_entity_part(entity)}_f{feature}_sf{system_function_id}")


def hvac_overrun_key(
    *,
    entity: Sequence[int],
    feature: int,
    overrun_id: int,
) -> str:
    """Stable key for an HVAC overrun (e.g. the one-time DHW boost)."""
    return slug(f"hvacoverrun_e{_entity_part(entity)}_f{feature}_ov{overrun_id}")
