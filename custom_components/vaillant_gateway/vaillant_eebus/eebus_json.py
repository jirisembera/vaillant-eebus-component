"""EEBUS JSON compatibility helpers.

Many SHIP/SPINE implementations don't send "plain" JSON objects on the wire.
Instead, they represent objects as a list of single-key objects, recursively.
The functions below convert between normal JSON and that array-wrapped format.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any


def json_into_eebus_json(payload: Any) -> str:
    """Convert standard JSON objects into the EEBUS array-wrapped JSON format.

    Mirrors ship-go's JsonIntoEEBUSJson():
    - Objects (dict) become arrays of single-key objects, recursively.
    - Arrays stay arrays.
    - The top-level array wrapper is stripped.
    """

    def _to_eebus(value: Any) -> Any:
        if isinstance(value, (dict, OrderedDict)):
            return [{k: _to_eebus(v)} for k, v in value.items()]
        if isinstance(value, list):
            return [_to_eebus(v) for v in value]
        return value

    converted = _to_eebus(payload)
    text = json.dumps(converted, separators=(",", ":"), ensure_ascii=False)

    if text.startswith("[") and text.endswith("]"):
        return text[1:-1]
    return text


def json_text_into_eebus_json(payload_text: str) -> str:
    """Convert a JSON text into EEBUS JSON, preserving field ordering."""
    parsed = json.loads(payload_text, object_pairs_hook=OrderedDict)
    return json_into_eebus_json(parsed)


def json_from_eebus_json(payload_text: str) -> str:
    """Convert EEBUS array-wrapped JSON into standard JSON.

    Uses ship-go's simple replacement strategy that works for SHIP/SPINE payloads
    and trims trailing NUL bytes (some devices append 0x00).
    """
    b = payload_text.encode("utf-8", errors="ignore")
    b = b.replace(b"[{", b"{")
    b = b.replace(b"},{", b",")
    b = b.replace(b"}]", b"}")
    b = b.replace(b"[]", b"{}")
    b = b.strip(b"\x00")
    return b.decode("utf-8", errors="ignore")
