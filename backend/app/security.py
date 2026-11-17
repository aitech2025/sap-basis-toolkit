import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from . import config

_api_key = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: str | None = Security(_api_key)) -> None:
    if not config.API_AUTH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API is locked: configure API_AUTH_TOKEN before using protected endpoints",
        )
    if not api_key or not secrets.compare_digest(api_key, config.API_AUTH_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
