"""Shared CLI plumbing imported as a top-level ``cli`` module by the tools.

The shared helpers (:func:`build_common_parser`, :func:`resolve_bind_address`,
:func:`resolve_log_level`, :func:`setup_logging`) are imported by the sibling
tools (``tools/pair.py``, ``tools/monitor.py``, ``tools/info.py``,
``tools/write.py``, ``tools/discover.py`` and the ``eebus_to_mqtt`` bridge).
This module is not itself runnable; the update streamer lives in
``tools/monitor.py``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from vaillant_eebus.mdns import resolve_interface_ipv4

_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def build_common_parser(
    *,
    prog: str,
    description: str,
    ship_options: bool = True,
) -> argparse.ArgumentParser:
    """Build an argparse parser carrying the shared --address/--interface,
    --mdns-timeout, the -v/-q ladder and --log-* flags.

    ``ship_options`` (default on) adds the flags only the connecting tools need —
    ``--mdns-port``, ``--ski`` and ``--cert-file``/``--key-file``. The read-only
    ``tools/discover.py`` listing passes ``ship_options=False`` to drop them.
    """
    p = argparse.ArgumentParser(prog=prog, description=description)

    net = p.add_argument_group("network")
    net.add_argument(
        "-a",
        "--address",
        metavar="IPv4",
        help="Optional local IPv4 to advertise via mDNS and pin zeroconf to. "
        "Omit to browse every interface and advertise on all of them. "
        "Mutually exclusive with --interface.",
    )
    net.add_argument(
        "-i",
        "--interface",
        metavar="IFACE",
        help="Optional local network interface name (e.g. eth0), resolved to its "
        "IPv4 address. Omit to browse every interface. Mutually exclusive with --address.",
    )
    if ship_options:
        net.add_argument(
            "--mdns-port",
            type=int,
            default=54885,
            help="Local SHIP service port advertised via mDNS (default: 54885).",
        )
    net.add_argument(
        "--mdns-timeout",
        type=int,
        default=30,
        help="Seconds to wait for the gateway's mDNS announcement (default: 30).",
    )
    if ship_options:
        net.add_argument(
            "--ski",
            metavar="SKI",
            default=None,
            help="Connect only to the Vaillant gateway with this SKI. Disambiguates "
            "when several Vaillant gateways are on the network (default: first found). "
            "List gateways and their SKIs with `python3 tools/discover.py`.",
        )

    log = p.add_argument_group("logging")
    log.add_argument(
        "-l",
        "--log-level",
        choices=_LEVELS,
        default=None,
        help="Logging level. Overrides --verbose/--quiet.",
    )
    log.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v: INFO, -vv: DEBUG, -vvv: include raw frame trace).",
    )
    log.add_argument(
        "-q",
        "--quiet",
        action="count",
        default=0,
        help="Decrease verbosity (-q: WARNING, -qq: ERROR).",
    )
    log.add_argument(
        "--log-format",
        default="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        help="logging.Formatter format string.",
    )

    if ship_options:
        cert = p.add_argument_group("certificate")
        cert.add_argument(
            "--cert-file",
            default="cert.pem",
            help="Path to client certificate PEM (created if missing). Default: cert.pem",
        )
        cert.add_argument(
            "--key-file",
            default="key.pem",
            help="Path to client private key PEM (created if missing). Default: key.pem",
        )

    return p


def resolve_log_level(args: argparse.Namespace) -> tuple[str, bool]:
    """Return (root_level, enable_trace) based on CLI flags.

    enable_trace switches on the very chatty raw frame echo logger
    (vaillant_eebus.ship.trace, vaillant_eebus.session.trace) — only enabled with -vvv or DEBUG.
    """
    if args.log_level:
        level = args.log_level
        return level, level == "DEBUG" and args.verbose >= 3
    if args.quiet >= 2:
        return "ERROR", False
    if args.quiet == 1:
        return "WARNING", False
    if args.verbose >= 3:
        return "DEBUG", True
    if args.verbose == 2:
        return "DEBUG", False
    return "INFO", False  # no flag or -v


def setup_logging(level: str, fmt: str, enable_trace: bool) -> None:
    """Configure the root logger to write everything to stderr."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(fmt))
    root.setLevel(level)
    root.addHandler(handler)
    # The "trace" sub-loggers carry the raw on-wire byte echo (`<` send, `>` recv).
    # Same treatment for the third-party `websockets` logger, whose DEBUG output
    # is just raw frame hex dumps. All silenced unless -vvv is explicit.
    trace_level = logging.DEBUG if enable_trace else logging.INFO + 1
    for name in ("vaillant_eebus.ship.trace", "vaillant_eebus.session.trace", "websockets"):
        logging.getLogger(name).setLevel(trace_level)


def resolve_bind_address(args: argparse.Namespace) -> Optional[str]:
    """Resolve --address/--interface to a bind IPv4, or None to autodiscover.

    None means: browse every interface and advertise on all of them.
    """
    if args.address and args.interface:
        raise SystemExit("--address and --interface are mutually exclusive")
    if args.address:
        return args.address
    if args.interface:
        try:
            return resolve_interface_ipv4(args.interface)
        except OSError as e:
            raise SystemExit(f"Could not resolve interface {args.interface!r}: {e}") from e
    return None
