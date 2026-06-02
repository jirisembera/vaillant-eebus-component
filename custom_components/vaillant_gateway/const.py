"""Constants for the Vaillant EEBUS integration."""

from __future__ import annotations

DOMAIN = "vaillant_gateway"

CONF_MDNS_TIMEOUT = "mdns_timeout"
CONF_DEVICE_SKI = "device_ski"
CONF_DEVICE_ADDRESS = "device_address"
CONF_CERT_PATH = "cert_path"
CONF_KEY_PATH = "key_path"
# Identity pulled from the gateway's mDNS TXT record at pairing time.
CONF_DEVICE_BRAND = "device_brand"
CONF_DEVICE_MODEL = "device_model"
CONF_DEVICE_SERIAL = "device_serial"

DEFAULT_MDNS_TIMEOUT = 30

# Brand fallback. Discovery gates on brand=Vaillant, so the TXT brand is always
# present in practice; this only guards a gateway that omitted it.
MANUFACTURER = "Vaillant"
# Shown when a gateway introduces itself without a model in its mDNS TXT. We never
# fabricate a real model number as a fallback.
UNKNOWN_MODEL = "<unknown>"

SERVICE_NAME_PREFIX = "Homeassistant"


def device_display_name(model: str, ski: str) -> str:
    """Registry device name: mDNS model (else placeholder) + short SKI.

    Manufacturer is a separate ``DeviceInfo`` field, so the name deliberately
    omits the brand to avoid "Vaillant Vaillant EEBUS".
    """
    return f"{model or UNKNOWN_MODEL} ({ski[:6]})"


def gateway_title(brand: str, model: str, ski: str) -> str:
    """Full brand + model + short-SKI label for config-flow titles.

    Unlike :func:`device_display_name`, this includes the brand because the
    config-flow UI shows no separate manufacturer field.
    """
    label = " ".join(part for part in (brand or MANUFACTURER, model or UNKNOWN_MODEL) if part)
    return f"{label} ({ski[:6]})" if ski else label
