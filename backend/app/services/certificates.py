from datetime import datetime, timezone
import socket
import ssl

from cryptography import x509
from cryptography.hazmat.backends import default_backend

from ..config import CERTIFICATE_TIMEOUT_SECONDS, DEFAULT_WARNING_DAYS
from .target_policy import resolve_approved_addresses


def _connect_pinned(addresses: list[str], port: int, timeout: float):
    last_error = None
    for address in addresses:
        try:
            return socket.create_connection((address, port), timeout=timeout)
        except OSError as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise OSError("No approved target addresses available")


def inspect_certificate(hostname: str, port: int = 443) -> dict:
    """Read the presented certificate using a DNS-resolved, policy-checked IP.

    The original hostname is retained for TLS SNI and hostname verification.
    Pinning the socket to the validated DNS answer prevents a second DNS lookup
    during connection establishment.
    """
    addresses = resolve_approved_addresses(hostname, port)
    context = ssl._create_unverified_context()
    with _connect_pinned(addresses, port, CERTIFICATE_TIMEOUT_SECONDS) as sock:
        with context.wrap_socket(sock, server_hostname=hostname) as tls:
            der_certificate = tls.getpeercert(binary_form=True)

    certificate = x509.load_der_x509_certificate(der_certificate, default_backend())
    now = datetime.now(timezone.utc)
    not_after = certificate.not_valid_after_utc
    not_before = certificate.not_valid_before_utc
    days_remaining = (not_after - now).days

    try:
        verified_context = ssl.create_default_context()
        with _connect_pinned(addresses, port, CERTIFICATE_TIMEOUT_SECONDS) as sock:
            with verified_context.wrap_socket(sock, server_hostname=hostname):
                tls_verified = True
        verification_error = None
    except (OSError, ssl.SSLError) as exc:
        tls_verified = False
        verification_error = str(exc)

    subject = certificate.subject.rfc4514_string()
    issuer = certificate.issuer.rfc4514_string()

    if days_remaining < 0:
        status = "expired"
    elif days_remaining <= 7:
        status = "critical"
    elif days_remaining <= DEFAULT_WARNING_DAYS:
        status = "warning"
    elif not tls_verified:
        status = "untrusted"
    else:
        status = "healthy"

    return {
        "subject": subject,
        "issuer": issuer,
        "serial_number": format(certificate.serial_number, "X"),
        "not_before": not_before.isoformat(),
        "not_after": not_after.isoformat(),
        "days_remaining": days_remaining,
        "tls_verified": tls_verified,
        "status": status,
        "error": verification_error,
    }
