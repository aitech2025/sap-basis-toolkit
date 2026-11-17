"""Inventory of certificates exported from SAP STRUST / PSEs.

Bridges the gap endpoint discovery cannot see (client PSEs, SSL client
identities, trusted CAs, SAML/WS-Security signing certificates). Basis exports
each certificate from STRUST (Base64 or binary) or uses `sapgenpse maintain_pk -l`
output saved as PEM, and uploads it here. Only public certificates are accepted.
"""
import hashlib
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from sqlalchemy import select

from .. import config
from ..db import get_db, imported_certificates
from .alerts import send_alert_once


class CertificateImportError(ValueError):
    pass


def parse_certificates(raw: bytes) -> list[x509.Certificate]:
    if b"PRIVATE KEY" in raw:
        raise CertificateImportError("Private key material detected; upload public certificates only")
    if b"-----BEGIN CERTIFICATE-----" in raw:
        try:
            return x509.load_pem_x509_certificates(raw)
        except ValueError as exc:
            raise CertificateImportError(f"Invalid PEM certificate data: {exc}") from exc
    stripped = raw.strip()
    try:
        return [x509.load_der_x509_certificate(stripped)]
    except ValueError:
        pass
    # STRUST "Base64" export without PEM armour
    try:
        import base64
        return [x509.load_der_x509_certificate(base64.b64decode(b"".join(stripped.split()), validate=True))]
    except Exception as exc:
        raise CertificateImportError("Unrecognised certificate format; use PEM, DER or Base64") from exc


def classify(days_remaining: int) -> str:
    if days_remaining < 0:
        return "expired"
    if days_remaining <= 7:
        return "critical"
    if days_remaining <= config.DEFAULT_WARNING_DAYS:
        return "warning"
    return "healthy"


def _with_status(row: dict) -> dict:
    item = dict(row)
    not_after = datetime.fromisoformat(item["not_after"])
    item["days_remaining"] = (not_after - datetime.now(timezone.utc)).days
    item["status"] = classify(item["days_remaining"])
    if hasattr(item.get("imported_at"), "isoformat"):
        item["imported_at"] = item["imported_at"].isoformat()
    return item


def import_certificates(raw: bytes, sap_system_id: str, pse_name: str) -> dict:
    certificates = parse_certificates(raw)
    created, skipped = [], 0
    with get_db() as connection:
        for cert in certificates:
            fingerprint = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
            exists = connection.execute(select(imported_certificates.c.id).where(
                imported_certificates.c.sap_system_id == sap_system_id,
                imported_certificates.c.pse_name == pse_name,
                imported_certificates.c.sha256_fingerprint == fingerprint,
            )).first()
            if exists:
                skipped += 1
                continue
            row = connection.execute(imported_certificates.insert().values(
                sap_system_id=sap_system_id, pse_name=pse_name,
                subject=cert.subject.rfc4514_string(), issuer=cert.issuer.rfc4514_string(),
                serial_number=format(cert.serial_number, "X"),
                not_before=cert.not_valid_before_utc.isoformat(),
                not_after=cert.not_valid_after_utc.isoformat(),
                sha256_fingerprint=fingerprint,
            ).returning(imported_certificates)).mappings().one()
            created.append(_with_status(row))
    return {"imported": len(created), "duplicates_skipped": skipped, "certificates": created}


def list_imported(sap_system_id: str | None = None) -> list[dict]:
    query = select(imported_certificates).order_by(imported_certificates.c.not_after)
    if sap_system_id:
        query = query.where(imported_certificates.c.sap_system_id == sap_system_id)
    with get_db() as connection:
        rows = connection.execute(query).mappings().all()
    return [_with_status(row) for row in rows]


def evaluate_imported_certificates() -> int:
    """Send threshold alerts for imported certificates; returns number of alertable certificates."""
    alertable = 0
    for cert in list_imported():
        days = cert["days_remaining"]
        thresholds = [-1] if days < 0 else [t for t in config.ALERT_THRESHOLDS_DAYS if 0 <= days <= t]
        if not thresholds:
            continue
        alertable += 1
        threshold = min(thresholds)  # most urgent crossed threshold only
        label = "expired" if threshold == -1 else f"{threshold}d"
        severity = "critical" if cert["status"] in ("expired", "critical") else "warning"
        send_alert_once(
            "strust-certificates",
            f"pse:{cert['sap_system_id']}:{cert['pse_name']}:{cert['sha256_fingerprint']}:{label}",
            f"{cert['status'].upper()}: {cert['sap_system_id']} PSE {cert['pse_name']} certificate "
            f"expires in {days} days",
            severity,
            {"sap_system_id": cert["sap_system_id"], "pse_name": cert["pse_name"], "subject": cert["subject"],
             "issuer": cert["issuer"], "not_after": cert["not_after"], "days_remaining": days},
        )
    return alertable
