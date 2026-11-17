# SAP Basis Toolkit

A lightweight, modular toolkit for SAP Basis operations. The first MVP is **SAP Basis Sentinel – Certificate Monitoring**.

## Current scope

- Register HTTPS endpoints.
- Inspect TLS certificate metadata and expiry.
- Run on-demand checks through a REST API.
- Store targets and check history in SQLite for local development.
- Keep the storage and collector boundaries ready for PostgreSQL and SAP STRUST/PSE integration.

This initial release monitors certificates presented by network endpoints. It does **not** claim to inventory certificates stored internally in SAP STRUST. That requires a separate SAP-side collector and is planned as a later integration.

## Quick start

Requires Python 3.11+.

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs for the API.

## API

- `GET /health`
- `POST /api/v1/targets`
- `GET /api/v1/targets`
- `POST /api/v1/targets/{target_id}/check`
- `GET /api/v1/checks`

Example target:

```json
{
  "name": "SAP Fiori QA",
  "hostname": "fiori-qa.example.com",
  "port": 443
}
```

## Development

```bash
cd backend
pytest
```

See [docs/architecture.md](docs/architecture.md) for boundaries and next steps.
