# SAP Basis Toolkit

A lightweight, modular toolkit for SAP Basis operations (SAP Basis Sentinel).

| Module | What it does | Input |
|---|---|---|
| Certificate monitor | Endpoint TLS expiry monitoring + STRUST/PSE certificate inventory, 90/60/30/14/7-day alerts | Live HTTPS probes; PEM/DER exports from STRUST |
| Table growth watchdog | Fastest-growing tables, disk/memory days-to-threshold forecast, housekeeping recommendations | DB02 / HANA `M_CS_TABLES` CSV snapshots + capacity figures |
| User & license optimizer | Dormant/never-used dialog users, SU10 lock list, license downgrade candidates with FUE estimate | USR02/SUIM export + optional ST03N usage export |
| Transport checker | Pre-import: collisions, sequence inversions, missing predecessors, protected table content, unreleased TRs, deletions, DDIC changes. Post-import: RC ≥ 8 alerts | E071/E070 exports, STMS import history or tp log |

Alerts go to a generic webhook, **Slack**, **Microsoft Teams** (Workflows/Adaptive Card), **PagerDuty** (Events API v2) and/or email. A built-in dashboard is served at `/`.

See [docs/product-evaluation.md](docs/product-evaluation.md) for the evaluation of the product ideas and the roadmap, and [samples/](samples/) for example SAP exports to try every module.

## Why export-based analyzers?

The growth, user and transport modules work from standard SE16/SUIM/ST03N/DB02 exports. This gives value on day one without installing an RFC SDK, creating privileged RFC users or passing a security review for write access to production. The analysis services are isolated behind plain functions so a live RFC/OData collector can feed them later without changing the logic. User and transport uploads are analysed in memory and never stored.

## Delivered: certificate monitoring API

- Register HTTPS endpoints and inspect their presented TLS certificates.
- Store certificate metadata and check history.
- Run scheduled sweeps in a dedicated worker, independent of API scaling.
- Configure 90/60/30/14/7-day expiry thresholds and webhook (generic/Slack/Teams), PagerDuty and email notifications.
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

## Toolkit quick start (curl)

    H="X-API-Key: $API_AUTH_TOKEN"; B=http://localhost:8000/api/v1
    # Table growth: upload a snapshot (repeat daily/weekly) and capacity, then report
    curl -XPOST "$B/growth/PRD/snapshots" -H "$H" --data-binary @samples/db02_snapshot_day1.csv
    curl -XPOST "$B/growth/PRD/capacity" -H "$H" -H 'content-type: application/json' -d '{"disk_total_gb":512,"disk_used_gb":438}'
    curl "$B/growth/PRD/report?window_days=30" -H "$H"
    # STRUST certificate inventory
    curl -XPOST "$B/certificates/import?sap_system_id=PRD&pse_name=SSL%20client%20Standard" -H "$H" --data-binary @client.crt
    # Users & licenses (JSON body with the CSV text; add ?format=csv for a lock list download)
    jq -n --rawfile u samples/usr02.txt --rawfile s samples/st03n_usage.csv '{usr02_csv:$u, usage_csv:$s}' |
      curl -XPOST "$B/users/analyze" -H "$H" -H 'content-type: application/json' -d @-
    # Transports
    jq -n --rawfile a samples/e071.csv --rawfile b samples/e070.csv '{e071_csv:$a, e070_csv:$b, queue:["DEVK900100","DEVK900110","DEVK900120"]}' |
      curl -XPOST "$B/transports/precheck" -H "$H" -H 'content-type: application/json' -d @-
    jq -n --rawfile l samples/stms_import_history.csv '{target_system:"QAS", log:$l}' |
      curl -XPOST "$B/transports/postcheck" -H "$H" -H 'content-type: application/json' -d @-

Exports may be comma, semicolon, tab or pipe delimited, with SAP (YYYYMMDD), German (DD.MM.YYYY) or ISO dates and German or English decimal separators. Common header variants are recognised (e.g. `BNAME`/`User`, `TRDAT`/`Last Logon`).

The scheduler worker also evaluates STRUST certificate thresholds and growth forecasts every `GROWTH_EVAL_INTERVAL_MINUTES` (default daily). Alerts are de-duplicated per certificate/threshold, per system/resource/week and per transport/RC.

If you enable PagerDuty or Slack/Teams, allow egress from the API and scheduler containers to those endpoints.

## API

- GET /health (public)
- POST /api/v1/targets
- GET /api/v1/targets
- PATCH /api/v1/targets/{target_id}/disable
- POST /api/v1/targets/{target_id}/check
- GET /api/v1/checks
- PATCH /api/v1/targets/{target_id}/enable
- GET /api/v1/notifications
- GET /api/v1/certificates/summary
- POST /api/v1/certificates/import?sap_system_id=&pse_name= (PEM/DER/Base64 body)
- GET /api/v1/certificates/imported
- POST /api/v1/growth/{sid}/snapshots (CSV body)
- POST /api/v1/growth/{sid}/capacity
- GET /api/v1/growth/systems
- GET /api/v1/growth/{sid}/report
- POST /api/v1/users/analyze
- POST /api/v1/transports/precheck
- POST /api/v1/transports/postcheck
- GET / (dashboard; API key entered in the page)

All management endpoints require `X-API-Key`. Run tests from `backend` with `pytest -q`.
