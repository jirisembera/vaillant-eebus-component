"""Tests for the small pure/env utilities (slug, env_*)."""

from __future__ import annotations

import pytest
from vaillant_eebus.utils import env_bool, env_int, env_str, slug


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("DHW Temperature", "dhw_temperature"),
        ("  Spaces  ", "spaces"),
        ("Mix3d-Ch@rs!", "mix3d_ch_rs"),
        ("___", "unknown"),
        ("", "unknown"),
        ("Σ Power", "power"),  # non-ascii run collapses, leading _ stripped
    ],
)
def test_slug(raw, expected):
    assert slug(raw) == expected


def test_env_str(monkeypatch):
    monkeypatch.delenv("VE_TEST", raising=False)
    assert env_str("VE_TEST", "def") == "def"
    monkeypatch.setenv("VE_TEST", "hello")
    assert env_str("VE_TEST", "def") == "hello"


def test_env_int(monkeypatch):
    monkeypatch.delenv("VE_INT", raising=False)
    assert env_int("VE_INT", 5) == 5
    monkeypatch.setenv("VE_INT", " 42 ")
    assert env_int("VE_INT", 5) == 42
    monkeypatch.setenv("VE_INT", "notint")
    assert env_int("VE_INT", 5) == 5  # parse failure falls back


@pytest.mark.parametrize(
    "val,expected",
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
    ],
)
def test_env_bool_recognised(monkeypatch, val, expected):
    monkeypatch.setenv("VE_BOOL", val)
    # default is the opposite, so a wrong return would be caught.
    assert env_bool("VE_BOOL", default=not expected) is expected


def test_env_bool_default_and_garbage(monkeypatch):
    monkeypatch.delenv("VE_BOOL", raising=False)
    assert env_bool("VE_BOOL", default=True) is True
    monkeypatch.setenv("VE_BOOL", "garbage")
    assert env_bool("VE_BOOL", default=True) is True  # unrecognised → default
