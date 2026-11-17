from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class TargetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    hostname: str = Field(min_length=1, max_length=253)
    port: int = Field(default=443, ge=1, le=65535)


class TargetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    hostname: str
    port: int
    enabled: bool
    created_at: str


class CertificateCheckRead(BaseModel):
    id: int
    target_id: int
    checked_at: str
    subject: str | None = None
    issuer: str | None = None
    serial_number: str | None = None
    not_before: datetime | None = None
    not_after: datetime | None = None
    days_remaining: int | None = None
    tls_verified: bool
    status: str
    error: str | None = None
