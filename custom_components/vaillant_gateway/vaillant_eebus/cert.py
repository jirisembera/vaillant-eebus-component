"""Client certificate identity.

The Vaillant trust/pairing flow ties trust to the client certificate's SKI.
We persist cert.pem/key.pem locally so the SKI stays stable across runs.
"""

from __future__ import annotations

import datetime
import logging
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)


def get_or_create_certificate(cert_file: str = "cert.pem", key_file: str = "key.pem") -> str:
    """Create or reuse a local client certificate. Return its SKI as hex."""
    if os.path.exists(cert_file) and os.path.exists(key_file):
        with open(cert_file, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        ski = cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest.hex()
        logger.debug("🔄 Reusing certificate (SKI: %s)", ski)
        return ski

    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EEBUS-Python-Client")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_file, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    ski = cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest.hex()
    logger.debug("📜 New certificate created (SKI: %s)", ski)
    return ski
