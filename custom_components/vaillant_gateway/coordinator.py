"""Runtime that owns a single :class:`EebusClient` for one config entry.

Push-based (notifies), so this is not a :class:`DataUpdateCoordinator`. A
single background task drives the SHIP/SPINE session through connect →
discover → read_values → subscribe → wait_closed, reconnects on failure with
exponential backoff, and fans out updates to platform-side listeners.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from homeassistant.components.zeroconf import async_get_async_instance
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_CERT_PATH,
    CONF_DEVICE_BRAND,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_SERIAL,
    CONF_DEVICE_SKI,
    CONF_KEY_PATH,
    CONF_MDNS_TIMEOUT,
    DEFAULT_MDNS_TIMEOUT,
    MANUFACTURER,
    SERVICE_NAME_PREFIX,
    device_display_name,
)
from .grouping import Grouping, compute_grouping
from .vaillant_eebus import (
    EebusClient,
    EebusError,
    HvacModeUpdate,
    MeasurementUpdate,
    SetpointUpdate,
    Topology,
    Update,
    discover_gateway,
    open_node,
)

_LOGGER = logging.getLogger(__name__)

# Listener signature: receives the update that just changed, or None when only
# availability flipped (so entities can refresh `available`).
Listener = Callable[[Optional[Update]], None]

_BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
_FIRST_READY_TIMEOUT = 60.0


@dataclass
class Snapshot:
    """Frozen view of the device topology used to build entities."""

    topology: Topology
    value_keys: frozenset
    grouping: Grouping
    update_by_key: Dict[str, Update] = field(default_factory=dict)


class VaillantGatewayRuntime:
    """Owns the client lifecycle for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.device_ski: str = entry.data[CONF_DEVICE_SKI]
        # Identity captured from mDNS at pairing time; fall back to defaults for
        # entries created before these keys existed (or a gateway that omitted them).
        self.device_brand: str = entry.data.get(CONF_DEVICE_BRAND) or MANUFACTURER
        # Empty when the gateway advertised no model in its mDNS TXT; the display
        # path then shows the "<unknown>" placeholder rather than guessing a model.
        self.device_model: str = entry.data.get(CONF_DEVICE_MODEL) or ""
        self.device_serial: str = entry.data.get(CONF_DEVICE_SERIAL) or ""
        # The richer model code (a hardware variant string) only comes from the
        # gateway's DeviceClassification; filled in once the topology is read.
        self.device_model_id: str = ""
        self.device_name: str = device_display_name(self.device_model, self.device_ski)

        self._mdns_timeout: int = entry.data.get(CONF_MDNS_TIMEOUT, DEFAULT_MDNS_TIMEOUT)
        self._cert_path: str = entry.data[CONF_CERT_PATH]
        self._key_path: str = entry.data[CONF_KEY_PATH]

        self._client: Optional[EebusClient] = None
        self._run_task: Optional[asyncio.Task] = None
        self._stopping = False

        self._listeners: List[Listener] = []
        self.latest: Dict[str, Update] = {}
        # Keys that have reported on the *current* connection. Reset on each
        # (re)connect; an entity whose key isn't live is reported unavailable,
        # so a feature lost on reconnect shows as N/A without taking the device
        # down. ``latest`` is kept across reconnects so values don't flicker.
        self.live_keys: set[str] = set()
        self.available = False
        self.snapshot: Optional[Snapshot] = None

        self._snapshot_ready = asyncio.Event()
        self._first_error: Optional[BaseException] = None

    # -- public to platforms ----------------------------------------------

    @property
    def client(self) -> EebusClient:
        if self._client is None:
            raise RuntimeError("EebusClient is not connected")
        return self._client

    def is_live(self, key: str) -> bool:
        """Whether ``key`` reported on the current connection (gates entity availability)."""
        return self.available and key in self.live_keys

    def async_add_listener(self, listener: Listener) -> Callable[[], None]:
        """Register an entity callback. Returns an unsubscribe."""
        self._listeners.append(listener)

        def _unsub() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _unsub

    # -- lifecycle --------------------------------------------------------

    async def async_start(self) -> None:
        """Start the background session task and wait for the first snapshot."""
        self._run_task = self.hass.loop.create_task(
            self._run_loop(), name=f"vaillant_gateway[{self.entry.entry_id}]"
        )
        try:
            await asyncio.wait_for(self._snapshot_ready.wait(), timeout=_FIRST_READY_TIMEOUT)
        except asyncio.TimeoutError as err:
            await self.async_stop()
            raise ConfigEntryNotReady(
                f"Timed out waiting for {self.device_name} to respond"
            ) from err
        if self._first_error is not None:
            await self.async_stop()
            raise ConfigEntryNotReady(str(self._first_error)) from self._first_error

    async def async_stop(self) -> None:
        self._stopping = True
        if self._run_task is not None:
            self._run_task.cancel()
            try:
                await self._run_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutting down
                pass
            self._run_task = None

    # -- internals --------------------------------------------------------

    def _set_available(self, available: bool) -> None:
        if self.available == available:
            return
        self.available = available
        for cb in list(self._listeners):
            try:
                cb(None)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("listener raised on availability change")

    def _on_update(self, update: Update) -> None:
        self.latest[update.key] = update
        self.live_keys.add(update.key)
        for cb in list(self._listeners):
            try:
                cb(update)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("listener raised on update %s", update.key)

    async def _run_loop(self) -> None:
        attempt = 0
        while not self._stopping:
            try:
                await self._run_once()
                attempt = 0  # clean disconnect; reset backoff
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                if not self._snapshot_ready.is_set():
                    self._first_error = err
                    self._snapshot_ready.set()  # unblock async_start so it can raise
                    return
                _LOGGER.warning("EEBUS session ended (%s); will retry", err)

            self._set_available(False)
            if self._stopping:
                return

            delay = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
            attempt += 1
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    async def _run_once(self) -> None:
        aiozc = await async_get_async_instance(self.hass)
        async with AsyncExitStack() as stack:
            node = await stack.enter_async_context(
                open_node(
                    aiozc=aiozc,
                    cert_file=self._cert_path,
                    key_file=self._key_path,
                    service_name_prefix=SERVICE_NAME_PREFIX,
                )
            )
            gateway = await discover_gateway(
                aiozc=node.aiozc,
                mdns_timeout=self._mdns_timeout,
                target_ski=self.device_ski,
            )
            client = await stack.enter_async_context(EebusClient(gateway, node=node))
            self._client = client
            # Fresh connection: only keys that report now are "live".
            self.live_keys = set()
            unsub = client.on_change(self._on_update)
            try:
                # One linear bring-up: handshake → discovery → descriptions →
                # values → subscribe. Raises only on a fatal (connect/discovery)
                # failure; a missing feature is logged and simply left out.
                await client.start(subscribe=True, read_values=True)

                if self.snapshot is None:
                    self._build_snapshot(client)

                self._set_available(True)
                if not self._snapshot_ready.is_set():
                    self._snapshot_ready.set()

                # Backfill any updates that arrived before listeners attached.
                for update in list(client.values.values()):
                    self.latest[update.key] = update

                await client.wait_closed()
            finally:
                unsub()
                self._client = None

    def _build_snapshot(self, client: EebusClient) -> None:
        """Capture the one-time device topology snapshot used to build entities.

        Called once, the first time the gateway reaches the ready state; the
        snapshot freezes the topology + value keys + grouping that the platform
        ``async_setup_entry`` functions read to instantiate entities.
        """
        topology = client.topology
        if topology is None:
            raise EebusError("client.topology is unavailable after discover()")
        # Prefer the gateway's own DeviceClassification identity over the coarser
        # mDNS TXT values captured at pairing. Runs once (snapshot is built once),
        # before the first entity — and thus DeviceInfo — is constructed.
        identity = topology.primary_identity()
        if identity is not None:
            self.device_brand = identity.manufacturer or self.device_brand
            self.device_model = identity.model or self.device_model
            self.device_model_id = identity.model_id or self.device_model_id
            self.device_serial = identity.serial or self.device_serial
            self.device_name = device_display_name(self.device_model, self.device_ski)
        keys = frozenset(client.values.keys())
        self.snapshot = Snapshot(
            topology=topology,
            value_keys=keys,
            grouping=compute_grouping(topology, keys),
            update_by_key=dict(client.values),
        )
        _LOGGER.info(
            "Gateway ready: %d values, %d climate, %d water_heater",
            len(keys),
            len(self.snapshot.grouping.climate_entities),
            len(self.snapshot.grouping.water_heater_entities),
        )


# Re-exports so platform modules only import from .coordinator.
__all__ = [
    "VaillantGatewayRuntime",
    "Snapshot",
    "MeasurementUpdate",
    "HvacModeUpdate",
    "SetpointUpdate",
    "Update",
]
