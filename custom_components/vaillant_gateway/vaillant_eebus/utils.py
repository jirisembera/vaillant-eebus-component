"""Small utilities shared across the package."""

from __future__ import annotations

import asyncio
import os
import re


class MsgCounter:
    """Async-safe SPINE msgCounter generator.

    SPINE datagrams contain a monotonically increasing msgCounter.
    We guard increments with a lock because messages may be sent from
    different async code paths.
    """

    def __init__(self, start: int = 1):
        self._value = start
        self._lock = asyncio.Lock()

    async def next(self) -> int:
        async with self._lock:
            value = self._value
            self._value += 1
            return value


def env_str(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v)


def env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return int(str(v).strip())
    except Exception:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean env var (1/0, true/false, yes/no, on/off)."""
    v = os.environ.get(name)
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def slug(s: str) -> str:
    """Turn a string into a safe MQTT/Home-Assistant object_id component."""
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"
