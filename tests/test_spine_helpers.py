"""Tests for the pure SPINE address/parse helpers."""

from __future__ import annotations

from vaillant_eebus.spine import (
    SpineAddress,
    first_cmd,
    make_reply_addresses,
    parse_spine_datagram,
    spine_addr,
)


def test_spine_addr():
    assert spine_addr(device="d-1", entity=1, feature=2) == SpineAddress((1,), 2, "d-1")


def test_spine_address_from_raw():
    assert SpineAddress.from_raw({"device": "gw", "entity": [1, 1], "feature": 3}) == SpineAddress(
        (1, 1), 3, "gw"
    )
    assert SpineAddress.from_raw({"entity": [4], "feature": 0}) == SpineAddress((4,), 0)
    # feature omitted → address with feature None (not rejected)
    assert SpineAddress.from_raw({"entity": [1, 1]}) == SpineAddress((1, 1), None)
    # missing / empty / non-int entity → None
    assert SpineAddress.from_raw({"feature": 2}) is None
    assert SpineAddress.from_raw({"entity": [], "feature": 2}) is None
    assert SpineAddress.from_raw({"entity": [1, "x"], "feature": 2}) is None
    assert SpineAddress.from_raw("nope") is None


def test_spine_address_feature_key():
    # The device-less (entity, feature) map key — None without a feature.
    assert SpineAddress((1, 1), 3, "gw").feature_key == ((1, 1), 3)
    assert SpineAddress((1, 1), None).feature_key is None


def test_spine_address_to_raw_and_with_device():
    addr = SpineAddress((1, 1), 2)
    # device-less destination omits "device"
    assert addr.to_raw() == {"entity": [1, 1], "feature": 2}
    bound = addr.with_device("gw")
    assert bound == SpineAddress((1, 1), 2, "gw")
    assert bound.to_raw() == {"device": "gw", "entity": [1, 1], "feature": 2}
    # entity-only address omits "feature"
    assert SpineAddress((4,)).to_raw() == {"entity": [4]}


def test_first_cmd_variants():
    assert first_cmd({"a": 1}) == {"a": 1}
    assert first_cmd([{"a": 1}]) == {"a": 1}
    assert first_cmd([[{"a": 1}]]) == {"a": 1}  # double array-wrapped
    assert first_cmd([]) is None
    assert first_cmd("x") is None
    assert first_cmd([[]]) is None
    assert first_cmd([5]) is None


def test_parse_spine_datagram():
    msg = {
        "data": {
            "payload": {
                "datagram": {
                    "header": {"msgCounter": 7, "cmdClassifier": "notify"},
                    "payload": {"cmd": [{"measurementListData": []}]},
                }
            }
        }
    }
    parsed = parse_spine_datagram(msg)
    assert parsed is not None
    hdr, cmd = parsed
    assert hdr["msgCounter"] == 7
    assert "measurementListData" in cmd


def test_parse_spine_datagram_bad_inputs():
    assert parse_spine_datagram({}) is None
    assert parse_spine_datagram({"data": {"payload": {"datagram": {"header": {}}}}}) is None


def test_make_reply_addresses_swaps_source_and_dest():
    hdr = {
        "addressSource": {"device": "gw", "entity": [1, 1], "feature": 1},
        "addressDestination": {"device": "me", "entity": [1], "feature": 2},
    }
    src, dst = make_reply_addresses(hdr, local_device_address="local-dev")
    # Destination of the reply = source of the request.
    assert dst == {"device": "gw", "entity": [1, 1], "feature": 1}
    # Source of the reply = destination of the request, with our device substituted.
    assert src == {"device": "local-dev", "entity": [1], "feature": 2}


def test_make_reply_addresses_does_not_inject_device_when_peer_omitted():
    # Interop rule: mirror what the peer sent — don't force-inject "device".
    hdr = {
        "addressSource": {"device": "gw", "entity": [1, 1], "feature": 1},
        "addressDestination": {"entity": [1], "feature": 2},  # no device
    }
    src, _dst = make_reply_addresses(hdr, local_device_address="local-dev")
    assert "device" not in src
    assert src == {"entity": [1], "feature": 2}


def test_make_reply_addresses_none_on_missing_addresses():
    assert make_reply_addresses({}, local_device_address="x") is None
    assert make_reply_addresses({"addressSource": {}}, local_device_address="x") is None
