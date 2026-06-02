"""High-level async client for a Vaillant EEBUS gateway.

Setup is three steps — local presence, discover, connect — then one linear
bring-up via :meth:`EebusClient.start`::

    async with open_node() as node:
        gateway = await discover_gateway(aiozc=node.aiozc)
        async with EebusClient(gateway, node=node) as hp:
            await hp.start(subscribe=True)        # bring-up + live notifications
            async for update in hp.updates():
                print(update.key, update.value, update.unit)

``start`` runs the unconditional bring-up (discovery + descriptions); the
``read_values`` and ``subscribe`` flags are opt-in and can also be invoked later
via :meth:`read_values` / :meth:`subscribe`. ``updates()`` is live-only — it does
not replay the :meth:`start` snapshot, so read :attr:`values` first if you need
the initial values. See ``docs/architecture.md`` (Public API) for the
callback / snapshot / write recipes and the bring-up state machine.

:meth:`start` raises :class:`vaillant_eebus.errors.EebusError` only if the
connection, handshake or discovery failed; a per-feature description/value gap is
logged and leaves that feature absent from :attr:`topology` / :attr:`values`
rather than failing the session.

The client owns the TLS WebSocket, the SHIP handshake, the SPINE receive loop,
and the per-feature handlers (the ``node`` carries our announcement + TLS).
Consumers see only :class:`vaillant_eebus.events.Update` records on the update stream
plus the structured :class:`vaillant_eebus.topology.Topology` snapshot.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from types import MappingProxyType
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Tuple,
    TypeVar,
    Union,
)

from .connection import (
    Connected,
    Gateway,
    LocalNode,
    open_connection,
)
from .errors import EebusError
from .events import HvacModeUpdate, HvacOverrunUpdate, SetpointUpdate, Update
from .handlers import (
    DeviceClassificationHandler,
    ElectricalHandler,
    FeatureHandler,
    HVACHandler,
    MeasurementHandler,
    SetpointHandler,
)
from .session import _SpineSession
from .spine import (
    LOCAL_CEM_ENTITY,
    LOCAL_FEATURE_ELECTRICAL,
    LOCAL_FEATURE_HVAC,
    LOCAL_FEATURE_MEASUREMENT,
    LOCAL_FEATURE_SETPOINT,
    SpineAddress,
    spine_addr,
)
from .topology import Topology, TopologyMaps, build_topology

logger = logging.getLogger(__name__)


Callback = Callable[[Update], Union[None, Awaitable[None]]]
_SENTINEL: Any = object()


class _WritableUpdate(Protocol):
    """An :class:`Update` that addresses a writable feature item.

    Both writable update types (:class:`SetpointUpdate` / :class:`HvacModeUpdate`)
    expose their SPINE source plus an ``item_id`` (the setpoint id / system-function
    id), so :meth:`EebusClient._resolve_target` reads the target id directly instead
    of by attribute-name string.
    """

    @property
    def source_entity(self) -> Tuple[int, ...]: ...
    @property
    def source_feature(self) -> int: ...
    @property
    def item_id(self) -> int: ...


_UpdateT = TypeVar("_UpdateT", bound=_WritableUpdate)


@dataclass(frozen=True)
class WriteTarget:
    """A resolved write target: which feature item a write addresses.

    ``item_id`` is a ``setpoint_id`` for setpoint writes and a
    ``system_function_id`` for HVAC-mode writes. The client's resolve helpers
    build one from either a cached stable key or an explicit triple, and the
    handler's ``build_write`` consumes the named fields — so the
    ``(entity, feature, id)`` triple never travels positionally past this point.
    """

    entity: Tuple[int, ...]
    feature: int
    item_id: int


# A write target as accepted from callers: the stable key string of a cached
# update, or an explicit ``(entity_tuple, feature, item_id)`` triple.
TargetSpec = Union[str, Tuple[Tuple[int, ...], int, int]]


def _normalize_target_triple(target: Any, id_field: str) -> WriteTarget:
    """Validate an explicit ``(entity_tuple, feature, item_id)`` write target."""
    if not (isinstance(target, tuple) and len(target) == 3):
        raise ValueError(
            f"Write target must be a stable key string or a "
            f"(entity_tuple, feature, {id_field}) tuple; got {target!r}"
        )
    entity, feature, item_id = target
    if isinstance(entity, int):
        entity_tuple: Tuple[int, ...] = (int(entity),)
    elif isinstance(entity, (list, tuple)) and all(isinstance(x, int) for x in entity):
        entity_tuple = tuple(int(x) for x in entity)
    else:
        raise ValueError(f"entity must be int or tuple of ints, got {entity!r}")
    if not isinstance(feature, int):
        raise ValueError(f"feature must be int, got {feature!r}")
    if not isinstance(item_id, int):
        raise ValueError(f"{id_field} must be int, got {item_id!r}")
    return WriteTarget(entity=entity_tuple, feature=int(feature), item_id=int(item_id))


@dataclass(frozen=True)
class _Handlers:
    """The per-feature handlers, typed.

    Holding them as named, concretely-typed fields (rather than a
    ``Dict[str, FeatureHandler]``) lets the client reach a specific handler
    without string keys or ``isinstance`` narrowing.
    """

    measurement: MeasurementHandler
    hvac: HVACHandler
    setpoint: SetpointHandler
    electrical: ElectricalHandler
    device_classification: DeviceClassificationHandler

    def ordered(self) -> List[FeatureHandler]:
        """Handlers as the session iterates them during bring-up.

        The order is not semantically significant: dispatch is keyed by each
        handler's disjoint ``handled_cmd_keys``, and the linear bring-up reads
        *every* handler's descriptions before *any* values, so Measurement
        updates already see the ElectricalConnection phase metadata.
        """
        return [
            self.measurement,
            self.hvac,
            self.setpoint,
            self.electrical,
            self.device_classification,
        ]

    def topology_maps(self) -> TopologyMaps:
        """Bundle the handlers' public description maps for ``build_topology``."""
        return TopologyMaps(
            measurements_by_key=self.measurement.description_map,
            setpoints_by_key=self.setpoint.description_map,
            setpoint_constraints_by_key=self.setpoint.constraint_map,
            system_functions_by_key=self.hvac.system_function_map,
            operation_modes_by_key=self.hvac.operation_mode_map,
            hvac_setpoint_relations_by_key=self.hvac.setpoint_relation_map,
            overruns_by_key=self.hvac.overrun_description_map,
            electrical_params_by_entity=self.electrical.parameter_map,
            device_classification_by_entity=self.device_classification.classification_map,
        )


class EebusClient:
    """High-level async client. See the module docstring for usage."""

    def __init__(
        self,
        gateway: Gateway,
        *,
        node: LocalNode,
        queue_maxsize: int = 1024,
    ) -> None:
        self._gateway = gateway
        self._node = node
        self._queue_maxsize = queue_maxsize

        self._values: Dict[str, Update] = {}
        self._callbacks: List[Callback] = []
        self._queues: List[asyncio.Queue] = []
        self._task: Optional[asyncio.Task] = None
        # Strong refs to in-flight tasks spawned for async on_change callbacks.
        # asyncio only holds a weak ref, so without this the task can be GC'd
        # mid-flight (and its exceptions swallowed). Discarded on completion.
        self._callback_tasks: "set[asyncio.Task]" = set()
        self._connected_event = asyncio.Event()
        self._error: Optional[BaseException] = None
        self._started = False

        self._session: Optional[_SpineSession] = None
        self._handlers: Optional[_Handlers] = None

    # -- public API --------------------------------------------------------

    @property
    def values(self) -> Mapping[str, Update]:
        """Latest update per stable key. Read-only view."""
        return MappingProxyType(self._values)

    @property
    def topology(self) -> Optional[Topology]:
        """Structured device topology, available once :meth:`start` has run."""
        if self._session is None or self._session.discovery is None:
            return None
        maps = self._handlers.topology_maps() if self._handlers is not None else TopologyMaps()
        return build_topology(
            discovery=self._session.discovery,
            device_address=self._session.remote_device_address,
            maps=maps,
        )

    def on_change(self, callback: Callback) -> Callable[[], None]:
        """Register a callback fired for every update. Returns an unsubscribe."""
        self._callbacks.append(callback)

        def _unsubscribe() -> None:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def updates(self) -> AsyncIterator[Update]:
        """Async iterator yielding each update as it arrives.

        Live only — it does not replay values read during :meth:`start`; call
        :meth:`read_values` for the current snapshot first if you need it.
        Iteration ends when the session terminates. Slow consumers risk drops if
        the per-iterator queue (``queue_maxsize``) fills.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        self._queues.append(q)
        return self._iterate(q)

    async def wait_closed(self) -> None:
        """Block until the underlying session terminates.

        If the session raised, that exception is re-raised here.
        """
        if self._task is None:
            return
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        if self._error is not None and not isinstance(self._error, asyncio.CancelledError):
            raise self._error

    # -- setup -------------------------------------------------------------

    async def _await_connected(self) -> None:
        """Block until the SHIP handshake completes; raise if it failed."""
        await self._connected_event.wait()
        if self._error is not None and not isinstance(self._error, asyncio.CancelledError):
            raise self._error
        if self._session is None:
            raise EebusError("session ended before connect completed")

    async def start(self, *, subscribe: bool = False, read_values: bool = False) -> None:
        """Bring the session up once, then leave it ready to use.

        Runs the linear setup — handshake → discovery → descriptions — and by
        default does nothing more. After it returns, :attr:`topology` is
        populated. ``read_values`` and ``subscribe`` are opt-in convenience
        flags: pass ``read_values=True`` to also read the initial values into
        :attr:`values`, and ``subscribe=True`` to register notifications so
        updates flow into :attr:`values` / :meth:`updates`. You can also opt in
        later with :meth:`read_values` / :meth:`subscribe`. Calling :meth:`start`
        again is a no-op.

        Raises :class:`vaillant_eebus.errors.EebusError` if the connection,
        handshake or discovery failed (there is no usable session then). A
        per-feature description/value gap is not fatal — it is logged and that
        feature is simply absent from :attr:`topology` / :attr:`values`.
        """
        if self._started:
            return
        await self._await_connected()
        assert self._session is not None
        await self._session.setup(subscribe=subscribe, read_values=read_values)
        self._started = True

    async def read_values(self) -> List[Update]:
        """Re-read every feature's current values and return the snapshot.

        Issues a fresh round of value reads, waits for the replies, and returns
        the resulting :attr:`values` as a list (same objects as :attr:`values`).
        Useful for a write read-back or a one-shot poll; prefer ``subscribe=True``
        notifications over polling this for live telemetry. Requires
        :meth:`start` to have run.
        """
        if self._session is None:
            raise EebusError("read_values() called before start()")
        await self._session.read_values()
        return list(self._values.values())

    async def subscribe(self) -> None:
        """Subscribe to live value notifications after :meth:`start`.

        Opt in to ``notify`` updates when you ran :meth:`start` without
        ``subscribe=True`` (e.g. brought the session up for a snapshot, then
        decided to keep it live). Requires :meth:`start` to have run.
        """
        if self._session is None:
            raise EebusError("subscribe() called before start()")
        await self._session.subscribe()

    # -- writes ------------------------------------------------------------

    async def write_setpoint(
        self,
        target: TargetSpec,
        value: float,
        *,
        timeout: float = 5.0,
    ) -> None:
        """Set a setpoint value (e.g. DHW target temperature).

        ``target`` is either the stable string key from
        :class:`vaillant_eebus.events.SetpointUpdate` (as found in :attr:`values`),
        or an explicit ``(entity_tuple, feature, setpoint_id)`` triple.

        Awaits the gateway's ``result`` reply. Raises
        :class:`vaillant_eebus.EebusWriteError` on a non-zero ``errorNumber``, on
        timeout, or if the session ends before the gateway answers. Raises
        :class:`ValueError` if the target cannot be resolved.
        """
        await self._await_connected()
        assert self._session is not None
        assert self._handlers is not None
        wt = self._resolve_target(target, update_type=SetpointUpdate, id_field="setpoint_id")
        frame = self._handlers.setpoint.build_write(
            entity_tuple=wt.entity,
            feature=wt.feature,
            setpoint_id=wt.item_id,
            value=value,
        )
        await self._session.write(frame, timeout=timeout)

    async def write_hvac_mode(
        self,
        target: TargetSpec,
        mode: Union[int, str],
        *,
        timeout: float = 5.0,
    ) -> None:
        """Select a new HVAC operation mode for a system function.

        ``target`` is either the stable string key from
        :class:`vaillant_eebus.events.HvacModeUpdate`, or an explicit
        ``(entity_tuple, feature, system_function_id)`` triple. ``mode`` is
        either the integer ``operationModeId`` or its name string
        (e.g. ``"heating"``); names are resolved against the per-feature
        operation-mode description map.

        Awaits the gateway's ``result`` reply. Raises
        :class:`vaillant_eebus.EebusWriteError` on a non-zero ``errorNumber``, on
        timeout, or if the session ends. Raises :class:`ValueError` if the
        target or mode cannot be resolved.
        """
        await self._await_connected()
        assert self._session is not None
        assert self._handlers is not None
        wt = self._resolve_target(target, update_type=HvacModeUpdate, id_field="system_function_id")
        operation_mode_id = self._handlers.hvac.resolve_operation_mode(wt.entity, wt.feature, mode)
        frame = self._handlers.hvac.build_write(
            entity_tuple=wt.entity,
            feature=wt.feature,
            system_function_id=wt.item_id,
            operation_mode_id=operation_mode_id,
        )
        await self._session.write(frame, timeout=timeout)

    async def write_overrun(
        self,
        target: TargetSpec,
        active: bool,
        *,
        timeout: float = 5.0,
    ) -> None:
        """Activate or cancel an HVAC overrun (e.g. the one-time DHW boost).

        ``target`` is either the stable string key from
        :class:`vaillant_eebus.events.HvacOverrunUpdate`, or an explicit
        ``(entity_tuple, feature, overrun_id)`` triple. ``active`` sets the
        overrun's status to ``active`` / ``inactive``.

        Awaits the gateway's ``result`` reply. Raises
        :class:`vaillant_eebus.EebusWriteError` on a non-zero ``errorNumber``, on
        timeout, or if the session ends. Raises :class:`ValueError` if the target
        cannot be resolved.
        """
        await self._await_connected()
        assert self._session is not None
        assert self._handlers is not None
        wt = self._resolve_target(target, update_type=HvacOverrunUpdate, id_field="overrun_id")
        frame = self._handlers.hvac.build_overrun_write(
            entity_tuple=wt.entity,
            feature=wt.feature,
            overrun_id=wt.item_id,
            active=active,
        )
        await self._session.write(frame, timeout=timeout)

    def _resolve_target(
        self,
        target: TargetSpec,
        *,
        update_type: type[_UpdateT],
        id_field: str,
    ) -> WriteTarget:
        """Resolve a write ``target`` to a :class:`WriteTarget`.

        A string is looked up as the stable key of a cached ``update_type``
        update (its source address + ``item_id`` become the target); an explicit
        triple is validated by :func:`_normalize_target_triple` (``id_field`` only
        names the id in its error messages).
        """
        if isinstance(target, str):
            update = self._values.get(target)
            if not isinstance(update, update_type):
                available = [k for k, v in self._values.items() if isinstance(v, update_type)]
                raise ValueError(
                    f"No {update_type.__name__} cached under key {target!r}; "
                    f"available keys: {available}"
                )
            return WriteTarget(
                entity=tuple(update.source_entity),
                feature=int(update.source_feature),
                item_id=int(update.item_id),
            )
        return _normalize_target_triple(target, id_field)

    async def __aenter__(self) -> "EebusClient":
        self._task = asyncio.create_task(self._run(), name="vaillant-eebus-client")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        for task in list(self._callback_tasks):
            task.cancel()
        self._wake_iterators()

    # -- emit / fan-out ----------------------------------------------------

    def _emit(self, update: Update) -> None:
        self._values[update.key] = update
        for cb in list(self._callbacks):
            try:
                result = cb(update)
                if inspect.isawaitable(result):
                    self._spawn_callback(result)
            except Exception:
                logger.exception("on_change callback raised")
        for q in list(self._queues):
            try:
                q.put_nowait(update)
            except asyncio.QueueFull:
                logger.warning("update queue full; dropping update %s", update.key)

    def _spawn_callback(self, coro: Awaitable[None]) -> None:
        """Run an awaitable on_change callback, keeping a strong task ref.

        asyncio holds only a weak reference to bare tasks, so we stash each in
        ``_callback_tasks`` until it finishes and log any exception it raised
        (which would otherwise be silently dropped).
        """

        async def _runner() -> None:
            try:
                await coro
            except Exception:
                logger.exception("async on_change callback raised")

        task = asyncio.ensure_future(_runner())
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)

    def _wake_iterators(self) -> None:
        for q in list(self._queues):
            try:
                q.put_nowait(_SENTINEL)
            except asyncio.QueueFull:
                pass

    async def _iterate(self, q: asyncio.Queue) -> AsyncIterator[Update]:
        try:
            while True:
                item = await q.get()
                if item is _SENTINEL:
                    return
                yield item
        finally:
            try:
                self._queues.remove(q)
            except ValueError:
                pass

    # -- session lifecycle -------------------------------------------------

    async def _run(self) -> None:
        try:
            async with open_connection(self._gateway, node=self._node) as conn:
                await self._spine_loop(conn)
        except asyncio.CancelledError:
            raise
        except EebusError as e:
            self._error = e
            logger.error("%s", e)
        except Exception as e:
            self._error = e
            logger.exception("Unhandled error in EebusClient")
        finally:
            # Make sure phase awaiters wake up even on failure.
            self._connected_event.set()
            if self._session is not None:
                close_exc = self._error or EebusError("session closed")
                self._session.fail_pending_writes(close_exc)
            self._wake_iterators()
            logger.debug("👋 EebusClient session ended.")

    # -- handler wiring ----------------------------------------------------

    def _build_handlers(self, local_device_address: str) -> _Handlers:
        def local_feature(feature: int) -> SpineAddress:
            return spine_addr(device=local_device_address, entity=LOCAL_CEM_ENTITY, feature=feature)

        electrical = ElectricalHandler(
            local_client_feature=local_feature(LOCAL_FEATURE_ELECTRICAL),
            on_update=self._emit,
        )
        measurement = MeasurementHandler(
            local_client_feature=local_feature(LOCAL_FEATURE_MEASUREMENT),
            on_update=self._emit,
            electrical=electrical,
        )
        hvac = HVACHandler(
            local_client_feature=local_feature(LOCAL_FEATURE_HVAC),
            on_update=self._emit,
        )
        setpoint = SetpointHandler(
            local_client_feature=local_feature(LOCAL_FEATURE_SETPOINT),
            on_update=self._emit,
        )
        # DeviceClassification is read-only metadata; it advertises no dedicated
        # local client feature, so source its reads from the Measurement client.
        # Dispatch routes replies by cmd_key, not by the local feature, so the
        # shared source is safe.
        device_classification = DeviceClassificationHandler(
            local_client_feature=local_feature(LOCAL_FEATURE_MEASUREMENT),
            on_update=self._emit,
        )
        return _Handlers(
            measurement=measurement,
            hvac=hvac,
            setpoint=setpoint,
            electrical=electrical,
            device_classification=device_classification,
        )

    # -- SPINE loop --------------------------------------------------------

    async def _spine_loop(self, conn: Connected) -> None:
        """Stand up the SPINE session and run its receive loop.

        The bring-up itself is driven by :meth:`start`; here we only build the
        handlers, create the session, signal that the handshake is done, and run
        the receive loop that feeds replies/notifies back to ``start``.
        """
        self._handlers = self._build_handlers(conn.local_device_address)
        self._session = _SpineSession(
            ws=conn.ws,
            local_device_address=conn.local_device_address,
            handlers=self._handlers.ordered(),
        )
        self._connected_event.set()
        await self._session.run_receive_loop()
