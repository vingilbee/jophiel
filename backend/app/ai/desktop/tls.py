"""The relay's TLS identity: one self-signed certificate, made on first use.

Controllers pin this exact certificate at pairing (see
``desktop_controller.link``), so it is never checked against a CA or a host
name, and the Mac's LAN address can change without breaking it. Deleting the
directory makes a new certificate, which un-pairs every controller: they will
refuse the new one until paired again. That is the intended way to rotate it.

Both the API (to show the fingerprint next to a pairing code) and the relay
call ``ensure_relay_certificate``; a file lock stops them racing to create two.
"""
from __future__ import annotations

import fcntl
import ipaddress
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.core.config import get_settings
from desktop_controller.link import cert_fingerprint

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CERT_NAME = "relay.crt"
KEY_NAME = "relay.key"


@dataclass(frozen=True)
class RelayCertificate:
    cert_path: Path
    key_path: Path
    fingerprint: str  # sha256 of the DER certificate, lowercase hex


def relay_dir() -> Path:
    path = Path(get_settings().desktop_relay_dir)
    return path if path.is_absolute() else BACKEND_ROOT / path


def ensure_relay_certificate(directory: Path | None = None) -> RelayCertificate:
    directory = directory or relay_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    cert_path, key_path = directory / CERT_NAME, directory / KEY_NAME
    with open(directory / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (cert_path.exists() and key_path.exists()):
            _create(cert_path, key_path)
    der = x509.load_pem_x509_certificate(cert_path.read_bytes()).public_bytes(serialization.Encoding.DER)
    return RelayCertificate(cert_path, key_path, cert_fingerprint(der))


def _create(cert_path: Path, key_path: Path) -> None:
    settings = get_settings()
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Columbina desktop relay")])
    now = datetime.now(timezone.utc)
    alt_names: list[x509.GeneralName] = [x509.DNSName("columbina-relay")]
    for host in {settings.desktop_controller_bind_host, settings.desktop_controller_public_host}:
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(host or "")))
        except ValueError:
            if host:
                alt_names.append(x509.DNSName(host))
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=5 * 365))
        # A CA so the controller can use this very certificate as its only
        # trust anchor (standard verification, no custom pinning code).
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        # Python's default contexts use X509 strict mode, which wants these on a CA.
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_bytes = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key_bytes)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
