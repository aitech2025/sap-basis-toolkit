from fastapi import APIRouter, HTTPException

from ..db import get_db
from ..schemas import TargetCreate
from ..services.certificates import inspect_certificate

router = APIRouter(prefix="/api/v1")


def _target(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "hostname": row["hostname"],
        "port": row["port"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
    }


def _check(row):
    return {
        "id": row["id"],
        "target_id": row["target_id"],
        "checked_at": row["checked_at"],
        "subject": row["subject"],
        "issuer": row["issuer"],
        "serial_number": row["serial_number"],
        "not_before": row["not_before"],
        "not_after": row["not_after"],
        "days_remaining": row["days_remaining"],
        "tls_verified": bool(row["tls_verified"]),
        "status": row["status"],
        "error": row["error"],
    }


@router.post("/targets", status_code=201)
def create_target(payload: TargetCreate):
    with get_db() as connection:
        try:
            cursor = connection.execute(
                "INSERT INTO targets (name, hostname, port) VALUES (?, ?, ?)",
                (payload.name, payload.hostname, payload.port),
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise HTTPException(409, "This hostname and port are already registered")
            raise
        row = connection.execute(
            "SELECT * FROM targets WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return _target(row)


@router.get("/targets")
def list_targets():
    with get_db() as connection:
        rows = connection.execute(
            "SELECT * FROM targets ORDER BY name"
        ).fetchall()
        return [_target(row) for row in rows]


@router.post("/targets/{target_id}/check")
def run_target_check(target_id: int):
    with get_db() as connection:
        target = connection.execute(
            "SELECT * FROM targets WHERE id = ? AND enabled = 1", (target_id,)
        ).fetchone()
        if target is None:
            raise HTTPException(404, "Enabled target not found")

        try:
            result = inspect_certificate(target["hostname"], target["port"])
        except Exception as exc:
            result = {
                "subject": None,
                "issuer": None,
                "serial_number": None,
                "not_before": None,
                "not_after": None,
                "days_remaining": None,
                "tls_verified": False,
                "status": "unreachable",
                "error": str(exc)[:1000],
            }

        cursor = connection.execute(
            """
            INSERT INTO certificate_checks
            (target_id, subject, issuer, serial_number, not_before, not_after,
             days_remaining, tls_verified, status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_id, result["subject"], result["issuer"],
                result["serial_number"], result["not_before"], result["not_after"],
                result["days_remaining"], int(result["tls_verified"]),
                result["status"], result["error"],
            ),
        )
        row = connection.execute(
            "SELECT * FROM certificate_checks WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return _check(row)


@router.get("/checks")
def list_checks(limit: int = 100):
    if limit < 1 or limit > 500:
        raise HTTPException(400, "limit must be between 1 and 500")
    with get_db() as connection:
        rows = connection.execute(
            "SELECT * FROM certificate_checks ORDER BY checked_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_check(row) for row in rows]
