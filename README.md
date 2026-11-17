# SAP Basis Toolkit

A multi-system SAP Basis operations platform (SAP Basis Sentinel) with a web UI and REST API. Register every SAP system in your landscape once, from ECC and S/4HANA to BW, CRM, Solution Manager, PI/PO and NetWeaver ABAP or Java, and run the same modules against all of them.

## Supported systems

The toolkit is product-agnostic: product rules live in data ([`profiles.py`](backend/app/services/profiles.py)) and can be extended with a JSON file (`PROFILE_OVERRIDES_FILE`) without code changes.

| Product | Stacks | Databases | Default licensing |
|---|---|---|---|
| S/4HANA, BW/4HANA | ABAP | HANA | FUE (S/4HANA), named user (BW/4HANA) |
| ERP 6.0 (ECC) | ABAP, dual | HANA, Oracle, Db2, MSSQL, MaxDB, ASE | Named user |
| BW 7.x, CRM, SRM, SCM, GRC, Gateway, other NetWeaver ABAP | ABAP | any | Named user |
| Solution Manager 7.2 | ABAP, Java, dual | any | Named user |
| PI/PO, Enterprise Portal, other NetWeaver Java | Java (PI/PO also dual) | any | Named user |

Each system records its SID, landscape (business unit, customer or track), environment (DEV/QAS/PRE/PRD/…), product version, SAP_BASIS release, host and default client. The same SID may exist in different landscapes. Its profile decides:

- **Licence vocabulary**: classic *Professional / Limited Professional / Employee*, or FUE *Advanced / Core / Self-Service* (overridable per system).
- **Module availability**: e.g. the E070/E071 transport checker applies to ABAP and dual stacks; Java systems are pointed to CTS+/NWDI. Java user analysis takes NWA/UME exports.
- **Export guide**: database-specific SQL or transactions for each module (HANA `M_CS_TABLES`, Oracle `DBA_SEGMENTS`, SQL Server DMVs, Db2 `ADMINTABINFO`, DB02 for the others; STRUST vs Java key storage).
- **Housekeeping catalogue**: Basis tables plus product extras (BW error logs and statistics, CRM BDocs, PI/PO message tables).

## Web UI

Served by the API at `/` (built into the Docker image):

- **Landscape overview**: every system grouped by landscape with combined health (certificates, capacity forecast, last import).
- **Landscapes**: each landscape drawn as product lanes (e.g. S/4HANA, BW/4HANA, ECC, Portal) across DEV → QAS → (TRN/PRE) → PRD columns, with the transport routes as arrows. Arrows carry the transport layer and the result of the last import into the target (OK, RC 4 warnings, RC ≥ 8 failed). Hover a system to trace its routes. Branches such as two development tracks or a training system are supported. Routes can be added manually or proposed automatically (*Suggest routes*: one system per tier → DEV → QAS → PRE → PRD).
- **Systems**: register, filter, edit and delete systems.
- **System workspace** (opened by clicking any system tile, table row, diagram node or SID link): an *Overview* dashboard of that system with a card per feature (certificates, table growth & capacity, users & licences, transports with its route path), recent alerts and analyses. Each card opens its tab. Tabs: setup and export guide, certificates (endpoints + STRUST/PSE), table growth (trend charts, forecast, housekeeping), users & licences, transports (pre/post checks) and history (summary-only run log + alerts).
- **Certificates** and **Alerts**: landscape-wide views.

The API key is entered in *Settings* and kept in browser session storage.

## Modules

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

## Local development

Backend (API on :8000):

Requires Python 3.11+ and Node.js 20+.

    cd backend
    python -m venv .venv
    # Windows: .venv\\Scripts\\activate
    # Linux/macOS: source .venv/bin/activate
    pip install -r requirements.txt
    export API_AUTH_TOKEN="replace-with-a-long-random-secret"
    export ALLOWED_TARGET_HOSTS="qa.example.test"
    uvicorn app.main:app --reload

UI (Vite dev server on :5173, proxies `/api` to :8000):

    cd frontend
    npm ci
    npm run dev        # or `npm run build` to emit into backend/app/static, served by FastAPI at /

For private SAP targets, also configure their network in `ALLOWED_TARGET_CIDRS`. Environment example files are templates; export their values or use your deployment's environment-file support.

## Database upgrades

Run from the backend directory:

    alembic upgrade head

The baseline migration supports fresh and earlier MVP databases. It creates missing tables and enforces uniqueness on `(hostname, port)` without deleting certificate history. If duplicates exist, migration stops with examples; resolve them and back up before retrying.

## Toolkit quick start (curl)

    H="X-API-Key: $API_AUTH_TOKEN"; B=http://localhost:8000/api/v1
    # Register a system (see GET $B/meta/catalogue for products, stacks, databases)
    curl -XPOST $B/systems -H "$H" -H 'content-type: application/json' \
      -d '{"sid":"S4P","name":"S/4HANA production","product":"S4HANA","stack":"ABAP","database":"HANA","environment":"PRD","landscape":"Finance"}'
    S=1  # id returned above
    curl -XPOST "$B/systems/$S/growth/snapshots" -H "$H" --data-binary @samples/db02_snapshot_day1.csv
    curl -XPOST "$B/systems/$S/growth/capacity" -H "$H" -H 'content-type: application/json' -d '{"disk_total_gb":512,"disk_used_gb":438}'
    curl "$B/systems/$S/growth/report?window_days=30" -H "$H"
    curl -XPOST "$B/systems/$S/certificates/import?pse_name=SSL%20client%20Standard" -H "$H" --data-binary @client.crt
    jq -n --rawfile u samples/usr02.txt --rawfile s samples/st03n_usage.csv '{usr02_csv:$u, usage_csv:$s}' |
      curl -XPOST "$B/systems/$S/users/analyze" -H "$H" -H 'content-type: application/json' -d @-
    jq -n --rawfile a samples/e071.csv --rawfile b samples/e070.csv '{e071_csv:$a, e070_csv:$b}' |
      curl -XPOST "$B/systems/$S/transports/precheck" -H "$H" -H 'content-type: application/json' -d @-
    curl "$B/overview" -H "$H"

Exports may be comma, semicolon, tab or pipe delimited, with SAP (YYYYMMDD), German (DD.MM.YYYY) or ISO dates and German or English decimal separators. Common header variants are recognised (e.g. `BNAME`/`User`, `TRDAT`/`Last Logon`).

The scheduler worker also evaluates STRUST certificate thresholds and growth forecasts every `GROWTH_EVAL_INTERVAL_MINUTES` (default daily). Alerts are de-duplicated per certificate/threshold, per system/resource/week and per transport/RC.

If you enable PagerDuty or Slack/Teams, allow egress from the API and scheduler containers to those endpoints.

## API

Interactive OpenAPI docs: `/docs`. Main groups:

- `GET /health` (public) · `GET /` UI
- Registry: `GET /api/v1/meta/catalogue`, `GET|POST /api/v1/systems`, `GET|PATCH|DELETE /api/v1/systems/{id}`, `GET /api/v1/landscapes`
- Transport routes: `GET|POST /api/v1/routes`, `DELETE /api/v1/routes/{id}`, `GET /api/v1/landscapes/{name}/topology`, `POST /api/v1/landscapes/{name}/routes/suggest[?apply=true]`, `GET /api/v1/systems/{id}/routes`
- Landscape: `GET /api/v1/overview`, `GET /api/v1/certificates/summary`, `GET /api/v1/alerts`
- Endpoints: `POST|GET /api/v1/targets` (with `system_id`), `PATCH /api/v1/targets/{id}`, `.../enable`, `.../disable`, `POST .../check`, `GET /api/v1/checks`
- Per system (`/api/v1/systems/{id}/…`): `certificates`, `certificates/import`, `certificates/{cert_id}` (DELETE), `growth/snapshots`, `growth/capacity`, `growth/report`, `users/analyze`, `transports/precheck`, `transports/postcheck`, `runs`

All management endpoints require `X-API-Key`. Run tests from `backend` with `pytest -q`.
