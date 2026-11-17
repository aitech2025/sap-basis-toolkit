# SAP Basis Toolkit

A lightweight, modular toolkit for SAP Basis operations. The first MVP is SAP Basis Sentinel – Certificate Monitoring.

## Delivered: certificate monitoring API

- Register HTTPS endpoints and inspect their presented TLS certificates.
- Store certificate metadata and check history.
- Schedule landscape-wide checks.
- Configure 90/60/30/14/7-day expiry thresholds.
- Send generic webhook alerts (including Slack/Teams incoming webhooks) and SMTP email.
- De-duplicate expiry and endpoint-unreachable alerts.
- Disable targets without deleting their monitoring history.
- Protect all management APIs with an API key.
- Use SQLite locally or PostgreSQL for shared deployments.
- Version the database schema with Alembic, including an upgrade path for databases created by earlier MVP versions.

**Scope note:** this release monitors certificates presented by network endpoints. It does not yet inventory all certificates stored internally in SAP STRUST/PSE. The SAP-specific collector is a separate integration requiring validation against the target SAP_BASIS release.

## Quick start

Requires Python 3.11+.

    cd backend
    python -m venv .venv
    # Windows: .venv\\Scripts\\activate
    # Linux/macOS: source .venv/bin/activate
    pip install -r requirements.txt

Set `API_AUTH_TOKEN` to a long random secret before starting the API:

    # Linux/macOS
    export API_AUTH_TOKEN="replace-with-a-long-random-secret"
    uvicorn app.main:app --reload

Open http://127.0.0.1:8000/docs for the API. Send the configured key in the `X-API-Key` header. `/health` remains public for health probes. If the token is missing, management APIs fail closed with HTTP 503.

Copy backend/.env.example to .env and export its settings into the process environment. For PostgreSQL set DATABASE_URL to postgresql+psycopg://sentinel:password@localhost:5432/sentinel.

## Database upgrades

Run migrations from the backend directory before deploying a release:

    alembic upgrade head

The baseline migration supports fresh databases and databases initialized by earlier MVP versions. It creates missing tables and enforces uniqueness on `(hostname, port)` without dropping certificate history. If an older database contains duplicate hostname/port pairs, migration stops with examples; resolve those duplicates before retrying. Back up the database before every production migration.

For new local development databases, the API bootstrap also creates the current schema. Alembic remains the deployment mechanism for controlled upgrades and schema version tracking.

## API

- GET /health (public)
- POST /api/v1/targets (X-API-Key required)
- GET /api/v1/targets (X-API-Key required)
- PATCH /api/v1/targets/{target_id}/disable (X-API-Key required)
- POST /api/v1/targets/{target_id}/check (X-API-Key required)
- GET /api/v1/checks (X-API-Key required)
- GET /api/v1/notifications (X-API-Key required)

Run tests with pytest from the backend directory. See docs/architecture.md for production notes.
