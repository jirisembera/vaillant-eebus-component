"""Base class for per-SPINE-feature handlers.

A :class:`FeatureHandler` owns the per-feature state that used to live as
locals in ``EebusClient._spine_loop``: the local client-feature address, the
list of remote servers, and the per-server description maps.

The session's linear ``setup`` (see :class:`vaillant_eebus.session._SpineSession`)
drives every handler through these steps in order:

1. :meth:`request_descriptions` — send description reads only (no subscribe,
   no value reads). Each arriving description reply decrements an internal
   counter; when it hits zero, :attr:`descriptions_ready` is set. The session
   waits on every handler's event (retrying laggards) before moving on, so by
   the time values are read every handler already has its complete description
   map and emitted Updates are born with full metadata (no post-hoc republish).
2. :meth:`request_values` — read current values; each reply decrements a second
   counter and sets :attr:`values_ready`. Callable again later via
   ``EebusClient.read_values`` for a write read-back / manual re-poll.
3. :meth:`subscribe` — register for value notifications (where applicable;
   :class:`ElectricalHandler` opts out via ``subscribe_on_kickoff = False``).
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, ClassVar, Dict, FrozenSet, List, Optional, Tuple

from ..events import Update
from ..parsers import extract_servers_by_type
from ..spine import FeatureKey, SpineAddress, SpineChannel
from ..spine_requests import request_remote_functions, subscribe_remote_feature

logger = logging.getLogger(__name__)

OnUpdate = Callable[[Update], None]

# ``FeatureKey`` is defined in :mod:`vaillant_eebus.spine` and re-exported here so
# the handler modules (and :mod:`vaillant_eebus.handlers`) keep importing it from
# ``.base`` as before.
__all__ = ["FeatureHandler", "FeatureKey", "OnUpdate"]


class FeatureHandler(ABC):
    """One handler per SPINE feature class (Measurement / HVAC / Setpoint / ElectricalConnection)."""

    feature_type: ClassVar[str] = ""
    handled_cmd_keys: ClassVar[FrozenSet[str]] = frozenset()
    # Subset of handled_cmd_keys whose reply completes the descriptions step.
    description_cmd_keys: ClassVar[FrozenSet[str]] = frozenset()
    # Extra functions read alongside the descriptions but NOT gating readiness:
    # static, best-effort metadata (e.g. setpoint↔mode relations). A gateway that
    # never answers these just leaves them absent — the feature still comes up.
    auxiliary_cmd_keys: ClassVar[FrozenSet[str]] = frozenset()
    # Cmd keys to read (per server) in the values step. Empty for read-only metadata.
    value_cmd_keys: ClassVar[FrozenSet[str]] = frozenset()
    # Whether the subscribe step issues a nodeManagementSubscriptionRequestCall per server.
    subscribe_on_kickoff: ClassVar[bool] = True

    def __init__(self, *, local_client_feature: SpineAddress, on_update: OnUpdate) -> None:
        self._local_client_feature = local_client_feature
        self._on_update = on_update
        self._servers: List[SpineAddress] = []
        self.descriptions_ready: asyncio.Event = asyncio.Event()
        self.values_ready: asyncio.Event = asyncio.Event()
        self._pending_descriptions = 0
        self._pending_values = 0
        # Set once a read-values pass has been kicked off (so values_ready
        # actually reflects "the requested reads landed", not "we never asked").
        self._values_requested = False

    @property
    def servers(self) -> List[SpineAddress]:
        """Server feature addresses this handler tracks (entity + feature id)."""
        return list(self._servers)

    def find_server(self, entity_tuple: Tuple[int, ...], feature: int) -> Optional[SpineAddress]:
        """Return the server :class:`SpineAddress` matching ``(entity_tuple, feature)``."""
        for server in self._servers:
            if server.entity == tuple(entity_tuple) and server.feature == int(feature):
                return server
        return None

    def set_servers_from_discovery(self, discovery: Dict[str, Any]) -> None:
        if self._servers:
            return
        servers = extract_servers_by_type(discovery, self.feature_type)
        if servers:
            self._servers = list(servers)
        # No servers, or no descriptions to fetch → phase A is trivially done.
        if not self._servers or not self.description_cmd_keys:
            self.descriptions_ready.set()
        # Likewise: nothing to read → values_ready is trivially true.
        if not self._servers or not self.value_cmd_keys:
            self.values_ready.set()

    async def request_descriptions(self, channel: SpineChannel) -> None:
        """Send description reads for every server.

        Replies arrive through :meth:`handle`, which decrements the pending
        counter and sets :attr:`descriptions_ready` when it reaches zero.
        Idempotent: a no-op once :attr:`descriptions_ready` is set.
        """
        if self.descriptions_ready.is_set():
            return
        keys = list(self.description_cmd_keys)
        self._pending_descriptions = len(self._servers) * len(keys)
        if self._pending_descriptions == 0:
            self.descriptions_ready.set()
            return
        # Auxiliary functions ride along in the same reads but are not counted
        # toward readiness (handle() only decrements for description_cmd_keys).
        read_names = keys + [k for k in self.auxiliary_cmd_keys if k not in keys]
        for server in self._servers:
            await request_remote_functions(
                channel,
                local_client_feature=self._local_client_feature,
                remote_server_feature=server,
                function_names=read_names,
            )

    async def subscribe(self, channel: SpineChannel) -> None:
        """Send a subscription request per server (no value reads).

        Handlers with :attr:`subscribe_on_kickoff` ``False`` (e.g.
        ElectricalConnection) skip this.
        """
        if not self.subscribe_on_kickoff:
            return
        for server in self._servers:
            await subscribe_remote_feature(
                channel,
                local_client_feature=self._local_client_feature,
                remote_server_feature=server,
                server_feature_type=self.feature_type,
            )

    async def request_values(self, channel: SpineChannel) -> None:
        """Issue a one-shot read of the current values across every server.

        :attr:`values_ready` is cleared, the reads are sent, and the event
        is set again once every reply has been processed by :meth:`handle`.
        Callable repeatedly for manual polling — each call resets the
        event and waits for a fresh round of replies.
        """
        keys = list(self.value_cmd_keys)
        if not self._servers or not keys:
            self._pending_values = 0
            self.values_ready.set()
            return
        self._values_requested = True
        self._pending_values = len(self._servers) * len(keys)
        self.values_ready.clear()
        for server in self._servers:
            await request_remote_functions(
                channel,
                local_client_feature=self._local_client_feature,
                remote_server_feature=server,
                function_names=keys,
            )

    def _source_key(
        self, hdr: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[FeatureKey]]:
        """An inbound frame's raw source address and its device-less feature key.

        Both come from ``hdr["addressSource"]``: subclasses key their per-feature
        description maps by the :class:`FeatureKey`, and pass the raw dict on to
        the list parsers (which stamp it as each emitted update's source). Returns
        ``(None, None)`` when the header carries no usable source address.
        """
        raw = hdr.get("addressSource") if isinstance(hdr, dict) else None
        addr = SpineAddress.from_raw(raw)
        return raw, addr.feature_key if addr is not None else None

    def handle(self, cmd_key: str, hdr: Dict[str, Any], cmd: Dict[str, Any]) -> None:
        """Orchestrator entry. Subclasses implement :meth:`_handle`."""
        self._handle(cmd_key, hdr, cmd)
        is_reply = isinstance(hdr, dict) and hdr.get("cmdClassifier") == "reply"
        if is_reply and cmd_key in self.description_cmd_keys:
            if self._pending_descriptions > 0:
                self._pending_descriptions -= 1
            if self._pending_descriptions == 0:
                self.descriptions_ready.set()
        if is_reply and cmd_key in self.value_cmd_keys and self._values_requested:
            if self._pending_values > 0:
                self._pending_values -= 1
            if self._pending_values == 0:
                self.values_ready.set()

    @abstractmethod
    def _handle(self, cmd_key: str, hdr: Dict[str, Any], cmd: Dict[str, Any]) -> None: ...
