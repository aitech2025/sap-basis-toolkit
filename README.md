# SAP Basis Toolkit

A lightweight, modular toolkit for SAP Basis operations. The first MVP is SAP Basis Sentinel – Certificate Monitoring.

## Delivered: certificate monitoring API

- Register HTTPS endpoints and inspect their presented TLS certificates.
- Store certificate metadata and check history.
- Run scheduled sweeps in a dedicated worker, independent of API scaling.
- Configure 90/60/30/14/7-day expiry thresholds and webhook/email notifications.
- De-duplicate expiry and endpoint-unreachable alerts.
- Disable targets without deleting monitoring history.
- Protect management APIs with an API key.
- Enforce hostname, destination-port and DNS/IP network allowlists.
- Deploy with Docker Compose and PostgreSQL.
- Manage schema upgrades with Alembic.

**Scope note:** this release monitors certificates presented by network endpoints. It does not yet inventory all certificates stored internally in SAP STRUST/PSE. The SAP-specific collector requires validation against the target SAP_BASIS release.

## Docker Compose deployment

Requires Docker Engine and the Docker Compose plugin.

1. Copy the root `.env.example` to `.env`.
2. Replace `POSTGRES_PASSWORD` and `API_AUTH_TOKEN` with strong unique secrets.
3. Set `ALLOWED_TARGET_HOSTS` to approved exact hostnames and/or wildcard subdomains.
4. For private SAP networks, set `ALLOWED_TARGET_CIDRS` to the narrowest possible ranges. Never use broad ranges such as `0.0.0.0/0`.
5. Set `ALLOWED_TARGET_PORTS` to the required HTTPS listener ports only (default 443; SAP examples may include 44300 or 8443).
6. Start with `docker compose up --build -d`.

The stack runs PostgreSQL, a one-shot Alembic migration, the API and one dedicated scheduler worker. The API does not start a scheduler, so it can scale horizontally without duplicating sweeps. Keep exactly one scheduler replica unless distributed scheduler locking is added.

The API is available at `http://localhost:8000`; send the configured key in `X-API-Key`. Use a TLS-terminating reverse proxy and restrict API network exposure for non-local access.

## Outbound target security

`ALLOWED_TARGET_HOSTS` is mandatory and supports exact hostnames, explicitly listed IP literals and wildcard subdomains such as `*.corp.example.com`. Wildcards match subdomains, not the apex domain. Empty allowlists fail closed. `ALLOWED_TARGET_PORTS` limits TCP destination ports.

Before each check, DNS is resolved and every returned IP is validated. The socket connects to a validated, pinned IP while TLS SNI and hostname validation continue to use the configured hostname. Public IPs are permitted only for allowlisted hosts. Private, loopback, link-local and other non-global addresses require a matching `ALLOWED_TARGET_CIDRS` entry. If CIDRs are configured, every DNS answer must fall inside one of them.

These controls are defense in depth, not a replacement for network egress filtering. Restrict API and scheduler container egress to approved networks and ports, and protect DNS infrastructure.

## Local Python development

Requires Python 3.11+.

    cd backend
    python -m venv .venv
    # Windows: .venv\\Scripts\\activate
    # Linux/macOS: source .venv/bin/activate
    pip install -r requirements.txt
    export API_AUTH_TOKEN="replace-with-a-long-random-secret"
    export ALLOWED_TARGET_HOSTS="qa.example.test"
    uvicorn app.main:app --reload

For private SAP targets, also configure their network in `ALLOWED_TARGET_CIDRS`. Environment example files are templates; export their values or use your deployment's environment-file support.

## Database upgrades

Run from the backend directory:

    alembic upgrade head

The baseline migration supports fresh and earlier MVP databases. It creates missing tables and enforces uniqueness on `(hostname, port)` without deleting certificate history. If duplicates exist, migration stops with examples; resolve them and back up before retrying.

## API

- GET /health (public)
- POST /api/v1/targets
- GET /api/v1/targets
- PATCH /api/v1/targets/{target_id}/disable
- POST /api/v1/targets/{target_id}/check
- GET /api/v1/checks
- GET /api/v1/notifications

All management endpoints require `X-API-Key`. Run tests from `backend` with `pytest -q`.
