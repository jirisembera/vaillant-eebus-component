"""Config flow for Vaillant EEBUS.

There is no IP/interface prompt, and the component never imports the
``zeroconf`` package itself — that would trip Home Assistant's "not using its
zeroconf" warning. All mDNS work happens on HA's shared, all-interface
``AsyncZeroconf`` (obtained via :func:`async_get_async_instance` and handed to
the vendored library through ``aiozc=``).

This is a discovery-only integration — there is no manual ``user`` step. A
gateway is only set up after Home Assistant sees its ``_ship._tcp.local.``
mDNS announcement; if HA can't see the gateway's announcement it also can't
see ours, so pairing (which relies on the same mDNS) would fail anyway.

Entry points:

* ``zeroconf`` — Home Assistant runs the ``_ship._tcp.local.`` browser itself
  (the manifest declares the type) and pushes each match here. We dedupe by
  SKI before pairing.
* ``confirm`` — user acknowledges a discovery; pairing still needs Trust.
* ``pair`` — runs :class:`EebusPairing` in a background task and uses
  :meth:`ConfigFlow.async_show_progress` to nag the user to press Trust in
  the myVAILLANT app.
"""

from __future__ import annotations

import asyncio
import logging
import os
from functools import partial
from typing import Any, Dict, Optional

from homeassistant.components.zeroconf import async_get_async_instance
from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    CONF_CERT_PATH,
    CONF_DEVICE_ADDRESS,
    CONF_DEVICE_BRAND,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_SERIAL,
    CONF_DEVICE_SKI,
    CONF_KEY_PATH,
    CONF_MDNS_TIMEOUT,
    DEFAULT_MDNS_TIMEOUT,
    DOMAIN,
    SERVICE_NAME_PREFIX,
    gateway_title,
)
from .vaillant_eebus import EebusPairing
from .vaillant_eebus.cert import get_or_create_certificate
from .vaillant_eebus.connection import Gateway, open_node
from .vaillant_eebus.errors import GatewayNotFoundError, HandshakeFailedError
from .vaillant_eebus.mdns import GATEWAY_BRAND

_LOGGER = logging.getLogger(__name__)


class VaillantGatewayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Vaillant EEBUS."""

    VERSION = 1

    def __init__(self) -> None:
        self._gateway_ski: Optional[str] = None
        self._mdns_timeout: int = DEFAULT_MDNS_TIMEOUT
        self._cert_path: Optional[str] = None
        self._key_path: Optional[str] = None
        self._client_ski: Optional[str] = None
        self._pair_task: Optional[asyncio.Task] = None
        self._pair_error: Optional[str] = None
        self._gateway = None

    def _device_label(self) -> str:
        """Brand + model + short-SKI label, from the discovered mDNS TXT."""
        g = self._gateway
        ski = (g.ski if g else self._gateway_ski) or ""
        return gateway_title(
            g.brand if g else "",
            g.model if g else "",
            ski,
        )

    # -- entry points ------------------------------------------------------

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> FlowResult:
        """Handle a SHIP service pushed by Home Assistant's zeroconf."""
        # Positively identify a Vaillant gateway — HA pushes every _ship service
        # (our own bridge, other clients), only Vaillant devices set brand.
        if (discovery_info.properties.get("brand") or "").lower() != GATEWAY_BRAND:
            return self.async_abort(reason="not_supported")
        ski = discovery_info.properties.get("ski")
        if not ski:
            return self.async_abort(reason="not_supported")
        # Dedupe against an already-configured gateway, then confirm.
        await self.async_set_unique_id(ski)
        self._abort_if_unique_id_configured(
            updates={CONF_DEVICE_ADDRESS: f"{discovery_info.host}:{discovery_info.port}"}
        )
        # HA's push already carries everything we need — build the Gateway
        # directly, no re-discovery before pairing.
        self._gateway_ski = ski
        self._gateway = Gateway(
            ip=discovery_info.host,
            port=discovery_info.port,
            ski=ski,
            brand=discovery_info.properties.get("brand") or "",
            model=discovery_info.properties.get("model") or "",
            name=discovery_info.properties.get("id") or "",
        )
        self.context["title_placeholders"] = {"name": self._device_label()}
        return await self.async_step_confirm()

    # -- step: confirm ----------------------------------------------------

    async def async_step_confirm(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        if user_input is not None:
            return await self.async_step_pair()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._device_label()},
        )

    # -- step: pair --------------------------------------------------------

    async def _prepare_cert_paths(self) -> None:
        base = os.path.join(self.hass.config.path("vaillant_gateway"), self.flow_id)
        # `exist_ok=True` so a retry within the same flow (which reuses
        # `flow_id`) doesn't trip over the directory left by the previous
        # attempt. async_add_executor_job can't pass kwargs, hence partial.
        await self.hass.async_add_executor_job(partial(os.makedirs, base, exist_ok=True))
        self._cert_path = os.path.join(base, "cert.pem")
        self._key_path = os.path.join(base, "key.pem")
        # Mint (or reuse) the cert up-front so we can show the operator
        # which SKI to look for in the myVAILLANT trust dialog. The same
        # cert/key files are reused by EebusPairing below — get_or_create
        # is idempotent.
        self._client_ski = await self.hass.async_add_executor_job(
            get_or_create_certificate, self._cert_path, self._key_path
        )

    async def _run_pairing(self) -> None:
        assert self._gateway and self._cert_path and self._key_path
        aiozc = await async_get_async_instance(self.hass)
        async with open_node(
            aiozc=aiozc,
            cert_file=self._cert_path,
            key_file=self._key_path,
            service_name_prefix=SERVICE_NAME_PREFIX,
        ) as node:
            self._gateway = await EebusPairing(self._gateway, node=node).run()

    def _pair_placeholders(self) -> Dict[str, str]:
        """Values interpolated into the `pair` step description / progress."""
        ski = self._client_ski or ""
        return {
            "client_name": f"{SERVICE_NAME_PREFIX}-{ski[:6]}" if ski else SERVICE_NAME_PREFIX,
            "client_ski": ski,
        }

    async def async_step_pair(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        if self._pair_task is None:
            await self._prepare_cert_paths()
            self._pair_task = self.hass.async_create_task(self._run_pairing())

        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair",
                progress_action="pairing",
                progress_task=self._pair_task,
                description_placeholders=self._pair_placeholders(),
            )

        try:
            await self._pair_task
        except GatewayNotFoundError:
            self._pair_error = "gateway_not_found"
            return self.async_show_progress_done(next_step_id="pair_failed")
        except HandshakeFailedError:
            self._pair_error = "handshake_failed"
            return self.async_show_progress_done(next_step_id="pair_failed")
        except Exception:  # noqa: BLE001
            _LOGGER.exception("pairing failed")
            self._pair_error = "unknown"
            return self.async_show_progress_done(next_step_id="pair_failed")
        finally:
            self._pair_task = None

        return self.async_show_progress_done(next_step_id="pair_finish")

    async def async_step_pair_failed(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        # Surface the failure on the confirm step so the operator can retry.
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._device_label()},
            errors={"base": self._pair_error or "unknown"},
        )

    async def async_step_pair_finish(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        assert self._gateway is not None
        await self.async_set_unique_id(self._gateway.ski)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=self._device_label(),
            data={
                CONF_MDNS_TIMEOUT: self._mdns_timeout,
                CONF_DEVICE_SKI: self._gateway.ski,
                CONF_DEVICE_ADDRESS: f"{self._gateway.ip}:{self._gateway.port}",
                CONF_DEVICE_BRAND: self._gateway.brand,
                CONF_DEVICE_MODEL: self._gateway.model,
                CONF_DEVICE_SERIAL: self._gateway.name,
                CONF_CERT_PATH: self._cert_path,
                CONF_KEY_PATH: self._key_path,
            },
        )
