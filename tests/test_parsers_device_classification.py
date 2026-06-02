"""Tests for the DeviceClassification parsers."""

from __future__ import annotations

from vaillant_eebus.parsers import (
    parse_device_classification_manufacturer_data,
    parse_device_classification_user_data,
)


def test_manufacturer_data_full():
    cmd = {
        "deviceClassificationManufacturerData": {
            "deviceName": "Example Heat Pump",
            "deviceCode": "EX-100",
            "serialNumber": "0000000001",
            "vendorName": "Example Group",
            "brandName": "Example",
        }
    }
    assert parse_device_classification_manufacturer_data(cmd) == {
        "deviceName": "Example Heat Pump",
        "deviceCode": "EX-100",
        "serialNumber": "0000000001",
        "vendorName": "Example Group",
        "brandName": "Example",
    }


def test_manufacturer_data_array_wrapped():
    cmd = {
        "deviceClassificationManufacturerData": [
            {"serialNumber": "0000000001", "brandName": "Example"}
        ]
    }
    assert parse_device_classification_manufacturer_data(cmd) == {
        "serialNumber": "0000000001",
        "brandName": "Example",
    }


def test_manufacturer_data_skips_blank_and_missing():
    cmd = {"deviceClassificationManufacturerData": {"deviceName": "  ", "brandName": "Example"}}
    assert parse_device_classification_manufacturer_data(cmd) == {"brandName": "Example"}


def test_user_data_label():
    cmd = {"deviceClassificationUserData": {"userLabel": "Zone 1"}}
    assert parse_device_classification_user_data(cmd) == {"userLabel": "Zone 1"}


def test_user_data_empty():
    assert parse_device_classification_user_data({"deviceClassificationUserData": {}}) == {}


def test_malformed_inputs_return_empty():
    assert parse_device_classification_manufacturer_data({}) == {}
    assert (
        parse_device_classification_manufacturer_data(
            {"deviceClassificationManufacturerData": "nope"}
        )
        == {}
    )
    assert parse_device_classification_user_data({}) == {}
    assert parse_device_classification_user_data({"deviceClassificationUserData": []}) == {}
