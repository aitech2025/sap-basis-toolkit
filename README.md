# SAP Basis Toolkit

A lightweight, modular toolkit for SAP Basis operations. The first MVP is SAP Basis Sentinel – Certificate Monitoring.

## Delivered: certificate monitoring API

- Register HTTPS endpoints and inspect their presented TLS certificates.
- Store certificate metadata and check history.
- Schedule landscape-wide checks.
- Configure 90/60/30/14/7-day expiry thresholds.
- Send generic webhook alerts (including Slack/Teams incoming webhooks) and SMTP email.
- De-duplicate alerts by certificate, threshold and channel.
- Use SQLite locally or PostgreSQL for shared deployments.

**Scope note:** this release monitors certificates presented by network endpoints. It does not yet inventory all certificates stored internally in SAP STRUST/PSE. The SAP-specific collector is a separate integration requiring validation against the target SAP_BASIS release.

## Quick start

Requires Python 3.11+.

    cd backend
    python -m venv .venv
    # Windows: .venv\\Scripts\\activate
    # Linux/macOS: source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn app.main:app --reload

Open http://127.0.0.1:8000/docs for the API.

Copy backend/.env.example to .env and export its settings into the process environment. For PostgreSQL set DATABASE_URL to postgresql+psycopg://sentinel:password@localhost:5432/sentinel.

## API

- GET /health
- POST /api/v1/targets
- GET /api/v1/targets
- POST /api/v1/targets/{target_id}/check
- GET /api/v1/checks
- GET /api/v1/notifications

Run tests with pytest from the backend directory. See docs/architecture.md for production notes.
