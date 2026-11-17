from pydantic import BaseModel, Field, ConfigDict, field_validator

class TargetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    hostname: str = Field(min_length=1, max_length=253)
    port: int = Field(default=443, ge=1, le=65535)
    @field_validator("hostname")
    @classmethod
    def normalize_hostname(cls, value: str) -> str:
        value = value.strip().rstrip(".").lower()
        if not value or any(ch.isspace() for ch in value) or "/" in value or "://" in value:
            raise ValueError("Provide a hostname or IP address, not a URL")
        return value

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
    not_before: str | None = None
    not_after: str | None = None
    days_remaining: int | None = None
    tls_verified: bool
    status: str
    error: str | None = None
