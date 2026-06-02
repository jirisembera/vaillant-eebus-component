"""Exception hierarchy for the Vaillant EEBUS client.

All errors derive from :class:`EebusError`, so callers can catch the base
class to handle any failure from discovery, the SHIP handshake, or a SPINE
write. The public ones (:class:`EebusError`, :class:`EebusWriteError`) are
re-exported from the package root; :class:`GatewayNotFoundError` and
:class:`HandshakeFailedError` are imported from here directly where needed.
"""

from __future__ import annotations

from typing import Optional


class EebusError(Exception):
    """Base class for EEBUS client errors."""


class GatewayNotFoundError(EebusError):
    """No gateway answered the mDNS discovery in time."""


class HandshakeFailedError(EebusError):
    """The SHIP handshake did not complete successfully."""


class EebusWriteError(EebusError):
    """A SPINE ``write`` did not succeed.

    Raised either when the gateway replied with a non-zero ``errorNumber`` or
    when no ``result`` reply arrived within the caller's timeout. Inspect
    :attr:`error_number` to distinguish — ``None`` means the write timed out
    or the session was torn down before the gateway answered.
    """

    def __init__(
        self,
        message: str,
        *,
        error_number: Optional[int] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.error_number = error_number
        self.description = description
