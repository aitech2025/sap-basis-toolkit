# SAP Basis Toolkit

An end-to-end SAP monitoring and Basis operations platform (SAP Basis Sentinel) with a web UI and REST API. Register every SAP system in your landscape once, from ECC and S/4HANA to BW, CRM, Solution Manager, PI/PO and NetWeaver ABAP or Java. Then:

- **Three-layer discovery from one OS login:** enter an application server and its root or `<sid>adm` login. Sentinel reads the SAP profiles and the OS over read-only SSH, follows `SAPDBHOST` to the database server, and records the application, database and operating-system layers. It then keeps them refreshed and shows them in the system's *Infrastructure* tab.
- **Choose what to monitor:** monitor all three layers (the default) or any combination of SAP application, database and operating system, per system. You can choose at discovery, when you add a system manually, or later. Collectors are set up and run only for the selected layers. See [docs/monitoring.md](docs/monitoring.md#choosing-the-monitoring-layers).
- **Auto-discovery:** enter a hostname or IP and the `<sid>adm` login. Sentinel finds the SAP Start Service, reads the SID, product, release and database, and maps every instance (ASCS/SCS, ERS, PAS, AAS, Java, Web Dispatcher). It then registers the system and starts monitoring.
- **Start / stop / restart:** whole systems (in start-priority order) or single instances through sapstartsrv. Includes live progress, SID confirmation, a mandatory reason, an audit trail, an optional maintenance window, and production opt-in.
- **Live monitoring:** availability, instances and work processes through the SAP Start Service (sapcontrol); HANA memory/CPU/disk, alerts, backups and system replication through read-only HANA SQL; and ABAP short dumps, system log, update errors, tRFC/qRFC and IDocs.
- **Incident management:** configurable alert rules (global, landscape or system), an incident lifecycle (open → acknowledged → resolved, with assignment, comments and timeline), auto-resolve, maintenance windows, and Slack/Teams/PagerDuty/email notifications.
- **Basis operations:** certificate expiry, table growth forecasts, user and licence optimisation, transport pre/post checks and landscape routes, background job failures and long-running locks.
- **Security:** named users with viewer/operator/admin roles, signed sessions, an audit log, encrypted collector credentials and an outbound host allowlist.

See [docs/monitoring.md](docs/monitoring.md) for collector setup, required SAP/HANA authorisations, rules and incidents. See [docs/monitoring-analytics.md](docs/monitoring-analytics.md) for historical analytics APIs, aggregation and data-quality semantics. See [docs/intelligent-operations.md](docs/intelligent-operations.md) for availability, anomaly detection, capacity projections and correlation methodology. See [docs/timeseries-storage.md](docs/timeseries-storage.md) for the telemetry storage architecture, migration and retention behaviour. See [docs/collector-audit.md](docs/collector-audit.md) for which collector covers the application, database and OS layers, how that was verified, and known limits.

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

Served by the API at `/` (built into the Docker image). The UI is React + TypeScript with [shadcn/ui](https://ui.shadcn.com) components (Radix primitives), Tailwind CSS and Recharts: soft rounded cards in a bento layout, KPI cards with sparklines, area and multi-system trend charts for all time-series data, a collapsible navigation rail, a breadcrumb top bar, a <kbd>Ctrl</kbd>/<kbd>⌘</kbd>+<kbd>K</kbd> command palette to jump to any SID or page, and light, dark or system themes. Status is always a coloured, shape-coded badge with text. Green, amber, red and grey are reserved for SAP state, and petrol is used for actions. Fonts (Plus Jakarta Sans and IBM Plex Mono) are bundled, so the UI works on networks without internet access.

- **Landscape overview**: every landscape laid out in transport order (DEV → QAS → PRD). Each system cell shows its health, running instances, open incidents and disk headroom. KPI tiles above summarise production health, incidents and instances.
- **Landscapes**: each landscape drawn as product lanes (e.g. S/4HANA, BW/4HANA, ECC, Portal) across DEV → QAS → (TRN/PRE) → PRD columns, with the transport routes as arrows. Arrows carry the transport layer and the result of the last import into the target (OK, RC 4 warnings, RC ≥ 8 failed). Hover a system to trace its routes. Branches such as two development tracks or a training system are supported. Routes can be added manually or proposed automatically (*Suggest routes*: one system per tier → DEV → QAS → PRE → PRD).
- **Discover system**: host + credentials → detected SID, product, database and instance roles → register and monitor.
- **Systems**: register, filter, edit and delete systems.
- **System workspace** (opened by clicking any system tile, table row, diagram node or SID link): an *Overview* dashboard of that system with a card per feature (certificates, table growth & capacity, users & licences, transports with its route path, transport history, background jobs, SAP locks), recent alerts and analyses. Each card opens its tab. Tabs include *Instances & control* (instance inventory with roles and status, start/stop/restart, operation history), setup and export guide, certificates (endpoints + STRUST/PSE), table growth (trend charts, forecast, housekeeping), users & licences, transports (pre/post checks) and history (summary-only run log + alerts).
- **Certificates** and **Alerts**: landscape-wide views.

Users sign in with their own account. The server API key can be used instead for automation or break-glass access.

## Modules

| Module | What it does | Input |
|---|---|---|
| Certificate monitor | Endpoint TLS expiry monitoring + STRUST/PSE certificate inventory, 90/60/30/14/7-day alerts | Live HTTPS probes; PEM/DER exports from STRUST |
| Table growth watchdog | Fastest-growing tables, disk/memory days-to-threshold forecast, housekeeping recommendations | DB02 / HANA `M_CS_TABLES` CSV snapshots + capacity figures |
| User & license optimizer | Dormant/never-used dialog users, SU10 lock list, license downgrade candidates with FUE estimate | USR02/SUIM export + optional ST03N usage export |
| Transport checker | Pre-import: collisions, sequence inversions, missing predecessors, protected table content, unreleased TRs, deletions, DDIC changes. Post-import: RC ≥ 8 alerts | E071/E070 exports, STMS import history or tp log |

Alerts go to a generic webhook, **Slack**, **Microsoft Teams** (Workflows/Adaptive Card), **PagerDuty** (Events API v2) and/or email. A built-in dashboard is served at `/`.

See [docs/product-evaluation.md](docs/product-evaluation.md) for the evaluation of the product ideas and the roadmap, and [samples/](samples/) for example SAP exports to try every module.


## Configuration & Security Analysis (new)

Configuration snapshots can be submitted by a future SAP-side extractor, a customer agent, or an authorized operator. Sentinel redacts fields whose names resemble passwords, secrets, tokens, private keys, credentials or API keys before persistence. It stores immutable snapshots and supports an admin-designated baseline.

- `POST /api/v1/systems/{id}/configuration/baseline` — admin-only; establishes or replaces the baseline.
- `POST /api/v1/systems/{id}/configuration/snapshots` — capture a current sanitized snapshot.
- `GET /api/v1/systems/{id}/configuration/drift` — added, removed and modified configuration paths compared with the baseline.
- `GET /api/v1/systems/{id}/configuration/snapshots` — recent snapshot history.

This is a generic configuration-drift engine, not yet a built-in SAP security-content pack. SAP-specific collectors, supported configuration items and compliance rules must be implemented and validated per SAP_BASIS release. Never upload private keys or unredacted secrets.

### Operations monitoring (per ABAP system)

| Feature | What it shows | Input |
|---|---|---|
| Transport import history | Every import into the system with RC, date, owner and description; filters for failed/warning; feeds the pre-check's missing-predecessor test automatically | STMS import history export, TPALOG, or tp/ALOG text (via the post-check upload) |
| Background job failures | Cancelled jobs grouped by job name with failure rate and "still failing vs recovered", recent cancellations, active jobs running longer than `JOB_LONG_RUNNING_MINUTES` (120). One alert per failing job per day | SM37 spreadsheet export or TBTCO; re-uploads update status (matched by job name + count) |
| Long-running SAP locks | Current locks, long-running (≥ `LOCK_WARNING_MINUTES` 60) and critical (≥ `LOCK_CRITICAL_MINUTES` 240) locks, oldest lock, locks persisting across snapshots, by user/table, lock count trend. Alerts per lock and severity | SM12 list export, uploaded as periodic snapshots (an empty list is a valid snapshot) |

SAP reports these times in the system's local time: set each system's **timezone** (IANA name, e.g. `Asia/Kolkata`) so lock ages and job durations are correct. Job and lock data older than `OPS_DATA_STALE_HOURS` (24) is flagged as stale and does not affect health. Data is retained for `OPS_RETENTION_DAYS` (90). Java systems show these features as not applicable (Java Scheduler and Locking Adapter are not covered).

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
- Deploy with Docker Compose and TimescaleDB (PostgreSQL-compatible); SQLite remains available for local development and CI.
- Manage schema upgrades with Alembic.

**Scope note:** this release monitors certificates presented by network endpoints. It does not yet inventory all certificates stored internally in SAP STRUST/PSE. The SAP-specific collector requires validation against the target SAP_BASIS release.

## Docker Compose deployment

Requires Docker Engine and the Docker Compose plugin.

1. Copy the root `.env.example` to `.env`.
2. Replace `POSTGRES_PASSWORD`, `API_AUTH_TOKEN` and `SECRET_KEY` (`openssl rand -hex 32`) with strong unique secrets.
3. Set `ALLOWED_TARGET_HOSTS` to approved exact hostnames and/or wildcard subdomains.
4. For private SAP networks, set `ALLOWED_TARGET_CIDRS` to the narrowest possible ranges. Never use broad ranges such as `0.0.0.0/0`.
5. Set `ALLOWED_TARGET_PORTS` to the required HTTPS listener ports only (default 443; SAP examples may include 44300 or 8443).
6. Start with `docker compose up --build -d`.

The stack runs PostgreSQL, a one-shot Alembic migration, the API and one dedicated scheduler worker. The API does not start a scheduler, so it can scale horizontally without duplicating sweeps. Keep exactly one scheduler replica unless distributed scheduler locking is added.

Open `http://localhost:8000`. On first start, create the administrator; first-run setup asks for the server API key. Then add users, register systems and add live collectors under each system's *Setup* tab. Scripts can keep using the API key (`X-API-Key`), which acts as an admin service account; people sign in with their own accounts so the audit log names them. Use a TLS-terminating reverse proxy and restrict API network exposure for non-local access.

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

- `GET /health` (public) · `GET /` UI · `GET /api/v1/auth/status`, `POST /api/v1/auth/setup`, `POST /api/v1/auth/login` (public)
- Users & audit: `GET /api/v1/me`, `POST /api/v1/me/password`, `GET|POST /api/v1/users`, `PATCH /api/v1/users/{id}`, `GET /api/v1/audit`
- Live monitoring: `GET|POST /api/v1/systems/{id}/collectors`, `POST /api/v1/systems/{id}/collectors/test`, `PATCH|DELETE /api/v1/collectors/{id}`, `POST /api/v1/collectors/{id}/run`, `GET /api/v1/systems/{id}/monitors[/{monitor}]`, `POST /api/v1/systems/{id}/monitors/{monitor}/upload`
- Incidents: `GET /api/v1/incidents`, `GET /api/v1/incidents/counts`, `GET /api/v1/incidents/{id}`, `POST /api/v1/incidents/{id}/{acknowledge|resolve|reopen|assign|comment}`, `GET|POST /api/v1/rules`, `PATCH|DELETE /api/v1/rules/{id}`, `GET /api/v1/rules/metrics`, `GET|POST /api/v1/maintenance`, `POST /api/v1/maintenance/{id}/end`
- Registry: `GET /api/v1/meta/catalogue`, `GET|POST /api/v1/systems`, `GET|PATCH|DELETE /api/v1/systems/{id}`, `GET /api/v1/landscapes`
- Transport routes: `GET|POST /api/v1/routes`, `DELETE /api/v1/routes/{id}`, `GET /api/v1/landscapes/{name}/topology`, `POST /api/v1/landscapes/{name}/routes/suggest[?apply=true]`, `GET /api/v1/systems/{id}/routes`
- Landscape: `GET /api/v1/overview`, `GET /api/v1/certificates/summary`, `GET /api/v1/alerts`
- Endpoints: `POST|GET /api/v1/targets` (with `system_id`), `PATCH /api/v1/targets/{id}`, `.../enable`, `.../disable`, `POST .../check`, `GET /api/v1/checks`
- Per system (`/api/v1/systems/{id}/…`): `overview`, `transports/history`, `jobs/snapshots`, `jobs/report`, `locks/snapshots`, `locks/report`, `certificates`, `certificates/import`, `certificates/{cert_id}` (DELETE), `growth/snapshots`, `growth/capacity`, `growth/report`, `users/analyze`, `transports/precheck`, `transports/postcheck`, `runs`

All other endpoints require a signed-in user (`Authorization: Bearer <token>`) or the API key (`X-API-Key`). Reads need viewer, changes need operator, and collectors/rules/users/audit need admin. Run tests from `backend` with `pytest -q`.
