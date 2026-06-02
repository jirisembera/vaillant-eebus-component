"""In-depth, *generic* SPINE discovery for a Vaillant EEBUS gateway.

``tools/info.py`` renders the typed :class:`Topology`, so it only shows detail
for the four feature classes the library has handlers for. This tool is the
opposite: it talks SPINE directly and is **feature-agnostic**. It reads the
gateway's detailed discovery, then for every feature it reads the functions the
device itself advertises in ``supportedFunction`` — and only those — issuing a
SPINE read for each and dumping whatever comes back. Nothing is guessed: a
feature that advertises no readable function is simply shown with no items. Use
it to see *everything* a gateway exposes — including features no handler exists
for yet, which is exactly the signal for "should we add a handler for this?".

It also reads NodeManagement's ``nodeManagementUseCaseData`` from ``[0]/0`` —
the EEBUS *use-case* discovery, which advertises which standardized use cases
(name + version + actor role + supported scenarios) the gateway implements. That
is the application-level companion to the feature-level tree: detailed discovery
says what features exist, use-case data says what application contracts they add
up to. Like detailed discovery itself, it is a fixed NodeManagement function, so
it is always requested rather than driven by ``supportedFunction``.

It deliberately does **not** go through :class:`EebusClient`: it drives a
minimal SPINE request/collect loop straight over the SHIP connection, so the
generic-probe logic stays out of the comm library's production path. The
trade-off vs. the handler path is extra reads (it does not reuse anything the
handlers cached) — fine for a one-shot diagnostic.

    python3 tools/detailed_discovery.py [-a IPv4 | -i IFACE] [--timeout N] [-vv]
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import textwrap
from typing import Any, Dict, List, Optional, Tuple

from cli import (
    build_common_parser,
    resolve_bind_address,
    resolve_log_level,
    setup_logging,
)
from vaillant_eebus.connection import Connected, discover_gateway, open_connection, open_node
from vaillant_eebus.eebus_json import json_from_eebus_json
from vaillant_eebus.ship import send_ship_json
from vaillant_eebus.spine import (
    SpineAddress,
    SpineChannel,
    parse_spine_datagram,
    send_spine_read,
    send_spine_result_ok,
    spine_addr,
)
from vaillant_eebus.spine_replies import handle_spine_read
from vaillant_eebus.spine_requests import request_remote_detailed_discovery
from vaillant_eebus.utils import MsgCounter

log = logging.getLogger("vaillant_eebus.detailed_discovery")

# Read this many seconds with no new frames after the function reads go out
# before deciding the gateway has answered everything it intends to.
_DRAIN_IDLE = 3.0
# Re-send a keep-alive hello after this many idle seconds so a slow gateway
# does not drop us mid-probe.
_KEEPALIVE_IDLE = 20.0

# Our advertised client features (see spine_replies.build_local_detailed_discovery)
# live under entity [1]. Source a read from the matching client feature where we
# have one; otherwise fall back to the Measurement client at [1] feature 1.
_CLIENT_FEATURE_BY_TYPE: Dict[str, int] = {
    "Measurement": 1,
    "Sensing": 2,
    "HVAC": 3,
    "Setpoint": 4,
    "ElectricalConnection": 5,
}
_DEFAULT_CLIENT_FEATURE = 1

# A reply/notify is filed under this key; the discovery reply also lands here.
_CollectKey = Tuple[Tuple[int, ...], int, str]


# ---------------------------------------------------------------------------
# Discovery payload helpers (raw dict in → addresses / functions out)
# ---------------------------------------------------------------------------


def _feature_descriptions(discovery: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every ``featureInformation[].description`` dict from a discovery payload."""
    out: List[Dict[str, Any]] = []
    feature_info = discovery.get("featureInformation")
    if isinstance(feature_info, list):
        for item in feature_info:
            if isinstance(item, dict) and isinstance(item.get("description"), dict):
                out.append(item["description"])
    return out


def _entity_feature(desc: Dict[str, Any]) -> Optional[Tuple[Tuple[int, ...], int]]:
    """Pull ``(entity_tuple, feature)`` from a feature description, or None."""
    faddr = desc.get("featureAddress")
    if not isinstance(faddr, dict):
        return None
    ent = faddr.get("entity")
    feat = faddr.get("feature")
    if not (isinstance(ent, list) and ent and all(isinstance(x, int) for x in ent)):
        return None
    if not isinstance(feat, int):
        return None
    return tuple(int(x) for x in ent), int(feat)


def _read_functions(desc: Dict[str, Any]) -> List[str]:
    """Readable function names the feature advertises in ``supportedFunction``.

    Strictly device-driven — nothing is guessed. A feature that advertises no
    readable function yields an empty list (and is shown with no items).
    """
    funcs: List[str] = []
    supported = desc.get("supportedFunction")
    if isinstance(supported, list):
        for entry in supported:
            if not isinstance(entry, dict):
                continue
            name = entry.get("function")
            if not isinstance(name, str):
                continue
            ops = entry.get("possibleOperations")
            # Readable unless it explicitly advertises ops without a "read".
            if isinstance(ops, dict) and ops and "read" not in ops:
                continue
            funcs.append(name)
    return funcs


def _source_for(local_device_address: str, feature_type: str) -> SpineAddress:
    """The local client feature address to source a read from, by feature type."""
    if feature_type == "NodeManagement":
        return spine_addr(device=local_device_address, entity=0, feature=0)
    feat = _CLIENT_FEATURE_BY_TYPE.get(feature_type, _DEFAULT_CLIENT_FEATURE)
    return spine_addr(device=local_device_address, entity=1, feature=feat)


# ---------------------------------------------------------------------------
# Minimal SPINE driver (recv-decode + ACK + reply-to-reads + collect)
# ---------------------------------------------------------------------------


async def _recv_spine(ws, *, timeout: float) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Receive one SPINE Data frame as ``(header, cmd)``; None on idle/other."""
    try:
        data = await asyncio.wait_for(ws.recv(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    if not (isinstance(data, (bytes, bytearray)) and len(data) > 0 and data[0] == 2):
        return None  # control / keep-alive / non-data frame — ignore for the probe
    try:
        text = json_from_eebus_json(bytes(data[1:]).decode("utf-8", errors="ignore"))
        message = json.loads(text)
    except Exception:
        return None
    return parse_spine_datagram(message)


class _Probe:
    """Drives one detailed-discovery probe over an open SHIP connection."""

    def __init__(self, conn: Connected) -> None:
        self._ws = conn.ws
        self._local = conn.local_device_address
        self._counter = MsgCounter(start=1)
        self.remote: Optional[str] = None
        self.discovery: Optional[Dict[str, Any]] = None
        self.collected: Dict[_CollectKey, Any] = {}

    async def _process(self, hdr: Dict[str, Any], cmd: Dict[str, Any]) -> None:
        """ACK, answer gateway reads, latch remote/discovery, file reply payloads."""
        src = SpineAddress.from_raw(hdr.get("addressSource"))
        if self.remote is None and src is not None and src.device:
            self.remote = src.device

        cls = hdr.get("cmdClassifier")
        if cls != "result":
            await send_spine_result_ok(
                self._ws,
                request_header=hdr,
                local_device_address=self._local,
                msg_counter=self._counter,
            )
        if cls == "read":
            await handle_spine_read(
                self._ws,
                request_header=hdr,
                cmd=cmd,
                local_device_address=self._local,
                msg_counter=self._counter,
            )
            return
        if cls not in ("reply", "notify"):
            return

        ent: Tuple[int, ...] = src.entity if src is not None else ()
        feat: Optional[int] = src.feature if src is not None else None
        for key, payload in cmd.items():
            if (
                key == "nodeManagementDetailedDiscoveryData"
                and self.discovery is None
                and isinstance(payload, dict)
            ):
                self.discovery = payload
            if feat is not None:
                self.collected[(ent, feat, key)] = payload

    async def _send_all_reads(self) -> int:
        """Issue a read for every readable function of every discovered feature.

        Returns the number of reads sent.
        """
        assert self.discovery is not None and self.remote is not None
        sent = 0
        for desc in _feature_descriptions(self.discovery):
            ef = _entity_feature(desc)
            if ef is None:
                continue
            ent, feat = ef
            ftype_raw = desc.get("featureType")
            ftype = ftype_raw if isinstance(ftype_raw, str) else ""
            functions = _read_functions(desc)
            if not functions:
                log.debug(
                    "entity=%s feature=%s type=%s: no readable functions", list(ent), feat, ftype
                )
                continue
            source = _source_for(self._local, ftype)
            destination = SpineAddress(entity=tuple(ent), feature=feat, device=self.remote)
            for function_name in functions:
                await send_spine_read(
                    self._ws,
                    address_source=source,
                    address_destination=destination,
                    cmd={function_name: {}},
                    msg_counter=self._counter,
                )
                sent += 1
        return sent

    async def _read_use_case_data(self) -> None:
        """Read NodeManagement's use-case discovery from the gateway's ``[0]/0``.

        Unlike the per-feature reads (driven by ``supportedFunction``), this is a
        fixed NodeManagement function — like detailed discovery itself — so it is
        always requested. The reply lands in ``self.collected`` under entity
        ``(0,)`` feature ``0`` and renders under NodeManagement (or, if the
        gateway omits that feature from discovery, in the leftover section).
        """
        assert self.remote is not None
        source = spine_addr(device=self._local, entity=0, feature=0)
        destination = spine_addr(device=self.remote, entity=0, feature=0)
        await send_spine_read(
            self._ws,
            address_source=source,
            address_destination=destination,
            cmd={"nodeManagementUseCaseData": {}},
            msg_counter=self._counter,
        )

    async def _keepalive(self) -> None:
        try:
            await send_ship_json(
                self._ws, {"connectionHello": {"phase": "ready", "waiting": 60000}}
            )
        except Exception:
            pass

    async def run(self, *, budget: float) -> None:
        """Latch the remote address, read detailed discovery, then read every
        advertised function and collect the replies until idle or out of budget.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget
        discovery_sent = False
        reads_sent = False
        last_activity = loop.time()
        last_keepalive = loop.time()

        while loop.time() < deadline:
            frame = await _recv_spine(self._ws, timeout=2.0)
            now = loop.time()
            if frame is not None:
                last_activity = now
                await self._process(*frame)
            elif now - last_keepalive > _KEEPALIVE_IDLE:
                await self._keepalive()
                last_keepalive = now

            if self.remote and not discovery_sent:
                await request_remote_detailed_discovery(
                    SpineChannel(
                        ws=self._ws,
                        local_device_address=self._local,
                        remote_device_address=self.remote,
                        msg_counter=self._counter,
                    )
                )
                # Both are fixed NodeManagement reads against [0]/0; fire the
                # use-case discovery alongside detailed discovery.
                await self._read_use_case_data()
                discovery_sent = True
            elif self.discovery is not None and not reads_sent:
                count = await self._send_all_reads()
                log.debug("Sent %d generic function reads", count)
                reads_sent = True
                last_activity = loop.time()

            if reads_sent and (loop.time() - last_activity) > _DRAIN_IDLE:
                return

        log.warning("⏰ Probe budget exhausted before the gateway went idle")


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


_DEFAULT_WIDTH = 120  # default wrap width for the compact tree
# Continuation lines repeat the tree prefix, then this extra indent so a wrapped
# line is visibly a continuation (it has no ├─/└─/· connector of its own).
_CONT_INDENT = "  "


def _branch(is_last: bool) -> str:
    return "└─ " if is_last else "├─ "


def _cont(is_last: bool) -> str:
    return "   " if is_last else "│  "


def _str_or_none(v: Any) -> Optional[str]:
    return v if isinstance(v, str) and v else None


def _wrap(head: str, cont_prefix: str, content: str, *, width: int) -> List[str]:
    """Wrap ``head + content`` to ``width``, prefixing continuations with
    ``cont_prefix`` (the tree bars with the connector replaced by indent).

    Wraps on whitespace, breaking a single over-long token only as a last resort
    so nothing overflows ``width``. No information is dropped (unlike truncation).
    """
    if not content:
        return [head.rstrip()]
    wrapped = textwrap.fill(
        content,
        width=max(width, len(cont_prefix) + 8),
        initial_indent=head,
        subsequent_indent=cont_prefix,
        break_long_words=True,
        break_on_hyphens=False,
    )
    return wrapped.split("\n")


def _compact(v: Any) -> str:
    """One-line ``k=v``/``[..]`` rendering of any JSON value."""
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}={_compact(val)}" for k, val in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_compact(x) for x in v) + "]"
    return str(v)


def _compact_entry(v: Any) -> str:
    """Like :func:`_compact` but without the outer ``{}`` for a top-level dict."""
    if isinstance(v, dict):
        return ", ".join(f"{k}={_compact(val)}" for k, val in v.items())
    return _compact(v)


def _list_payload(payload: Any) -> Optional[Tuple[str, List[Any]]]:
    """If ``payload`` is the common ``{xData: [...]}`` shape, return (key, list)."""
    if isinstance(payload, dict) and len(payload) == 1:
        ((key, value),) = payload.items()
        if isinstance(value, list):
            return key, value
    return None


def _entity_addr(entity_address: Any) -> Optional[Tuple[int, ...]]:
    if not isinstance(entity_address, dict):
        return None
    ent = entity_address.get("entity")
    if isinstance(ent, list) and ent and all(isinstance(x, int) for x in ent):
        return tuple(int(x) for x in ent)
    return None


def _entities_with_features(
    discovery: Dict[str, Any],
) -> List[Tuple[Tuple[int, ...], Optional[str], Optional[str], List[Dict[str, Any]]]]:
    """Group discovery into ``(entity, type, description, [feature descs])``, sorted."""
    meta: Dict[Tuple[int, ...], Tuple[Optional[str], Optional[str]]] = {}
    entity_info = discovery.get("entityInformation")
    if isinstance(entity_info, list):
        for item in entity_info:
            desc = item.get("description") if isinstance(item, dict) else None
            if not isinstance(desc, dict):
                continue
            ent = _entity_addr(desc.get("entityAddress"))
            if ent is not None:
                meta[ent] = (
                    _str_or_none(desc.get("entityType")),
                    _str_or_none(desc.get("description")),
                )

    feats_by_entity: Dict[Tuple[int, ...], List[Dict[str, Any]]] = {}
    for desc in _feature_descriptions(discovery):
        ef = _entity_feature(desc)
        if ef is not None:
            feats_by_entity.setdefault(ef[0], []).append(desc)

    out = []
    for ent in sorted(set(meta) | set(feats_by_entity)):
        etype, edesc = meta.get(ent, (None, None))
        feats = sorted(
            feats_by_entity.get(ent, []), key=lambda d: (_entity_feature(d) or (ent, 0))[1]
        )
        out.append((ent, etype, edesc, feats))
    return out


def _device_summary(discovery: Dict[str, Any]) -> Optional[str]:
    info = discovery.get("deviceInformation")
    desc = info.get("description") if isinstance(info, dict) else None
    if not isinstance(desc, dict):
        return None
    bits: List[str] = []
    for key in ("brandName", "deviceModel", "deviceType"):
        value = _str_or_none(desc.get(key))
        if value:
            bits.append(value)
    return " · ".join(bits) or None


def _group_by_feature(probe: _Probe) -> Dict[Tuple[Tuple[int, ...], int], Dict[str, Any]]:
    """Collected replies regrouped as ``{(entity, feature): {function: payload}}``."""
    by_feature: Dict[Tuple[Tuple[int, ...], int], Dict[str, Any]] = {}
    for (ent, feat, key), payload in probe.collected.items():
        by_feature.setdefault((ent, feat), {})[key] = payload
    return by_feature


def _function_lines(
    func_name: str, payload: Any, *, prefix: str, is_last: bool, width: int
) -> List[str]:
    """Render one function reply: a header line + one wrapped line per entry."""
    # The discovery reply *is* this whole tree — don't echo it back as a leaf.
    if func_name == "nodeManagementDetailedDiscoveryData":
        return [prefix + _branch(is_last) + f"{func_name}  (→ the tree above)"]

    listed = _list_payload(payload)
    if listed is None:
        body = _compact_entry(payload)
        sep = ": " if body else ""
        return _wrap(
            prefix + _branch(is_last),
            prefix + _cont(is_last) + _CONT_INDENT,
            f"{func_name}{sep}{body}",
            width=width,
        )

    _, items = listed
    lines = [prefix + _branch(is_last) + f"{func_name} [{len(items)}]"]
    item_prefix = prefix + _cont(is_last)
    for item in items:
        lines.extend(
            _wrap(
                item_prefix + "· ",
                item_prefix + "  " + _CONT_INDENT,
                _compact_entry(item),
                width=width,
            )
        )
    return lines


def render_tree(probe: _Probe, *, width: int = _DEFAULT_WIDTH) -> List[str]:
    """Render a compact Device → Entity → Feature → function tree.

    Long entry lines wrap to ``width`` with indented continuations rather than
    being truncated, so no detail is lost.
    """
    discovery = probe.discovery or {}
    by_feature = _group_by_feature(probe)

    lines: List[str] = [f"📡 Device: {probe.remote or '(unknown)'}"]
    summary = _device_summary(discovery)
    if summary:
        lines.append(f"   {summary}")

    entities = _entities_with_features(discovery)
    rendered: set = set()
    for i, (ent, etype, edesc, feats) in enumerate(entities):
        e_last = i == len(entities) - 1
        head = f"Entity {list(ent)}  {etype or '(unknown type)'}"
        if edesc:
            head += f' — "{edesc}"'
        lines.append(_branch(e_last) + head)
        e_cont = _cont(e_last)
        if not feats:
            lines.append(e_cont + "└─ (no features)")
            continue
        for j, desc in enumerate(feats):
            f_last = j == len(feats) - 1
            ef = _entity_feature(desc)
            assert ef is not None
            rendered.add(ef)
            feat = ef[1]
            ftype = desc.get("featureType") or "(unknown type)"
            role = desc.get("role") or "?"
            lines.append(e_cont + _branch(f_last) + f"{ftype} (f{feat}, {role})")
            f_cont = e_cont + _cont(f_last)
            payloads = by_feature.get(ef, {})
            funcs = sorted(payloads)
            if not funcs:
                lines.append(f_cont + "└─ (no reply)")
                continue
            for k, fn in enumerate(funcs):
                lines.extend(
                    _function_lines(
                        fn, payloads[fn], prefix=f_cont, is_last=k == len(funcs) - 1, width=width
                    )
                )

    # Replies for an address not present in discovery (shouldn't happen, but surface it).
    leftover = sorted(by_feature.keys() - rendered)
    for ent, feat in leftover:
        lines.append(_branch(True) + f"Entity {list(ent)} feature {feat}  (not in discovery)")
        payloads = by_feature[(ent, feat)]
        funcs = sorted(payloads)
        for k, fn in enumerate(funcs):
            lines.extend(
                _function_lines(
                    fn, payloads[fn], prefix="   ", is_last=k == len(funcs) - 1, width=width
                )
            )
    return lines


def _dump(payload: Any) -> str:
    try:
        return json.dumps(payload, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(payload)


def render_raw(probe: _Probe) -> List[str]:
    """Full, untruncated dump: raw discovery + every per-feature reply as JSON."""
    lines: List[str] = []
    discovery = probe.discovery or {}

    lines.append("=" * 72)
    lines.append("RAW nodeManagementDetailedDiscoveryData")
    lines.append("=" * 72)
    lines.append(_dump(discovery))
    lines.append("")
    lines.append("=" * 72)
    lines.append("PER-FEATURE FUNCTION READS")
    lines.append("=" * 72)

    by_feature = _group_by_feature(probe)

    seen: set = set()
    for desc in _feature_descriptions(discovery):
        ef = _entity_feature(desc)
        if ef is None or ef in seen:
            continue
        seen.add(ef)
        ent, feat = ef
        ftype = desc.get("featureType") or "(unknown type)"
        role = desc.get("role") or "?"
        lines.append("")
        lines.append(f"▸ entity {list(ent)} feature {feat} — {ftype} (role={role})")
        payloads = by_feature.get(ef, {})
        if not payloads:
            lines.append("    (no reply)")
            continue
        for key in sorted(payloads):
            lines.append(f"  • {key}:")
            for body_line in _dump(payloads[key]).splitlines():
                lines.append(f"      {body_line}")

    for ef in sorted(by_feature.keys() - seen):
        ent, feat = ef
        lines.append("")
        lines.append(f"▸ entity {list(ent)} feature {feat} — (not in discovery)")
        for key in sorted(by_feature[ef]):
            lines.append(f"  • {key}:")
            for body_line in _dump(by_feature[ef][key]).splitlines():
                lines.append(f"      {body_line}")

    return lines


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


async def _gather(
    *,
    bind_address: Optional[str],
    mdns_timeout: int,
    mdns_port: int,
    cert_file: str,
    key_file: str,
    budget: float,
    target_ski: Optional[str],
) -> _Probe:
    async with open_node(
        cert_file=cert_file,
        key_file=key_file,
        bind_address=bind_address,
        mdns_port=mdns_port,
    ) as node:
        gateway = await discover_gateway(
            aiozc=node.aiozc, mdns_timeout=mdns_timeout, target_ski=target_ski
        )
        async with open_connection(gateway, node=node) as conn:
            probe = _Probe(conn)
            await probe.run(budget=budget)
            return probe


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_common_parser(
        prog="vaillant-eebus-detailed-discovery",
        description=(
            "Deep, feature-agnostic SPINE probe: connect, read detailed discovery "
            "+ use-case data, read every function each feature advertises, dump the "
            "raw replies, exit."
        ),
    )
    probe_opts = parser.add_argument_group("probe")
    probe_opts.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Seconds budget for the probe once connected (default: 20).",
    )
    probe_opts.add_argument(
        "--width",
        type=int,
        default=_DEFAULT_WIDTH,
        help=f"Wrap width for the compact tree; long lines wrap (with indented "
        f"continuations) rather than truncate (default: {_DEFAULT_WIDTH}).",
    )
    probe_opts.add_argument(
        "--raw",
        action="store_true",
        help="Dump the full JSON of discovery + every reply instead of the compact tree.",
    )
    args = parser.parse_args(argv)

    level, enable_trace = resolve_log_level(args)
    setup_logging(level, args.log_format, enable_trace)
    bind_address = resolve_bind_address(args)

    try:
        probe = asyncio.run(
            _gather(
                bind_address=bind_address,
                mdns_timeout=args.mdns_timeout,
                mdns_port=args.mdns_port,
                cert_file=args.cert_file,
                key_file=args.key_file,
                budget=args.timeout,
                target_ski=args.ski,
            )
        )
    except KeyboardInterrupt:
        log.info("👋 Interrupted by user")
        return 130
    except Exception as e:
        log.error("❌ Probe failed: %s", e)
        return 1

    if probe.discovery is None:
        log.error("❌ No detailed discovery received (gateway never answered)")
        return 1

    lines = render_raw(probe) if args.raw else render_tree(probe, width=args.width)
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
