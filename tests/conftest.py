"""Shared fixtures for the vaillant_eebus pure-logic tests.

The library is imported as a top-level ``vaillant_eebus`` package via the
committed ``tools/vaillant_eebus`` symlink (``pythonpath = ["tools"]`` in
pyproject.toml), exactly how the CLI tools consume it.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def discovery():
    """A minimal but realistic NodeManagementDetailedDiscoveryData payload.

    One heat-pump entity ``[1, 1]`` carrying one server feature of each class
    (Measurement / Setpoint / HVAC / ElectricalConnection), plus a bare
    DeviceInformation entity ``[1]`` with no features.
    """
    return {
        "specificationVersionList": {"specificationVersion": ["1.3.0"]},
        "entityInformation": [
            {
                "description": {
                    "entityAddress": {"entity": [1, 1]},
                    "entityType": "HeatPumpAppliance",
                    "description": "Heat pump",
                }
            },
            {
                "description": {
                    "entityAddress": {"entity": [1]},
                    "entityType": "DeviceInformation",
                    "description": "Device info",
                }
            },
        ],
        "featureInformation": [
            {
                "description": {
                    "featureAddress": {"entity": [1, 1], "feature": 1},
                    "featureType": "Measurement",
                    "role": "server",
                    "description": "Meas",
                }
            },
            {
                "description": {
                    "featureAddress": {"entity": [1, 1], "feature": 2},
                    "featureType": "Setpoint",
                    "role": "server",
                    "description": "SP",
                }
            },
            {
                "description": {
                    "featureAddress": {"entity": [1, 1], "feature": 3},
                    "featureType": "HVAC",
                    "role": "server",
                    "description": "HVAC",
                }
            },
            {
                "description": {
                    "featureAddress": {"entity": [1, 1], "feature": 4},
                    "featureType": "ElectricalConnection",
                    "role": "server",
                    "description": "EC",
                }
            },
        ],
    }
