# SAP Basis Sentinel – architecture

## Delivered

- HTTPS endpoint certificate discovery and X.509 metadata extraction.
- Separate trust and hostname validation.
- On-demand checks and a dedicated blocking scheduler worker.
- Configurable expiry thresholds (default 90/60/30/14/7 days).
- Webhook and SMTP notifications with event history and deduplication.
- Endpoint-unreachable alerts, de-duplicated per outage and re-armed after recovery.
- Target disable operation preserving target and check history.
- API-key protection for management endpoints; /health remains public.
- Explicit hostname, destination-port and DNS/IP network policy.
- SQLAlchemy storage for SQLite development and PostgreSQL deployment.
- Alembic baseline migration and Docker Compose deployment.

## Scheduler topology

FastAPI does not start a scheduler. Run one instance of `python -m app.worker`; it performs an immediate sweep and then repeats every `CHECK_INTERVAL_MINUTES`. API replicas can scale independently. Do not run multiple scheduler replicas without distributed locking or leader election.

## Target security policy

`ALLOWED_TARGET_HOSTS` is mandatory and accepts exact hostnames, explicitly listed IP literals and wildcard subdomains such as `*.corp.example.com` (not the apex domain). `ALLOWED_TARGET_PORTS` defaults to TCP 443; add SAP HTTPS listener ports only when needed. Registration fails closed if the hostname or port is not allowed.

Before every check, all DNS answers are validated. The socket is pinned to a validated IP while TLS SNI and hostname verification use the original hostname. Non-global addresses (private, loopback, link-local, etc.) require a matching `ALLOWED_TARGET_CIDRS` entry. When CIDRs are configured, every DNS answer must fall inside an approved range.

Application controls do not replace network egress filtering. Restrict API and scheduler egress to approved target networks and ports, and protect DNS infrastructure.

## Database migrations

Run `alembic upgrade head` before deployment. The baseline creates missing tables and adds a unique index on hostname/port. It does not delete history. Legacy duplicates cause a clear migration failure; resolve them and back up before retrying.

## SAP STRUST/PSE boundary

Endpoint TLS discovery sees only certificates presented during a network handshake. It cannot inventory all certificates stored in STRUST, including client PSEs and certificates not presented by an endpoint. The included collector is a contract only; a production RFC adapter must be built and tested against the supported SAP_BASIS release and a least-privilege SAP interface. Do not treat the current scaffold as working STRUST integration.

## Multi-system model (v0.5)

- `sap_systems` is the registry (SID + landscape unique). Every module table references it: `targets.system_id` (nullable, SET NULL on delete), `imported_certificates`, `table_size_snapshots`, `capacity_snapshots`, `analysis_runs`, `alert_events` (CASCADE).
- `services/profiles.py` holds product/stack/database rules, licence models, export guides and housekeeping catalogues. `PROFILE_OVERRIDES_FILE` (JSON) adds products or housekeeping entries.
- Disabling a system pauses its endpoint checks, PSE alerts and growth alerts without deleting history.
- `analysis_runs` stores summary counts of stateless analyses (no user names, no uploaded content) for the overview and history views.
- `GET /api/v1/overview` derives per-system health: worst of certificate status, capacity forecast (≤7 days critical, ≤`GROWTH_ALERT_DAYS` warning) and last import post-check.

## Transport routes and landscape topology

- `transport_routes` (source → target system, `consolidation` | `delivery`, optional transport layer) mirrors STMS (ABAP) or CTS+ (Java) routes. Deleting a system removes its routes.
- Validation: no self-routes, no duplicates, no cycles, no backwards routes by tier (SBX < DEV < QAS < TRN/PRE < PRD), and no mixing of ABAP and Java. Routes across landscapes or products are allowed but reported as warnings.
- `GET /landscapes/{name}/topology` returns tiers (columns), lanes (one per product and stack), routes enriched with the last import post-check of the target, per-system health and a readable path per lane.
- `suggest_routes` proposes a DEV → QAS → PRE → PRD chain (plus QAS → TRN) per product where each tier holds exactly one system, and leaves ambiguous tracks to the user.
- The UI places system cards in a CSS grid and draws routes in an SVG overlay from measured card positions. Routes that would cross a card on a skipped tier are drawn underneath the row.

## Operations monitoring

- `transport_imports`: one row per import into a system (request, RC, time, owner, text), filled by post-check uploads, de-duplicated on request + time + RC (untimed log rows within a day). `imported_requests()` feeds the pre-check's missing-predecessor test.
- `job_runs`: SM37/TBTCO rows upserted by job name + job count (or start time), so a job moving from active to finished/cancelled updates in place. New cancellations alert once per job name per day.
- `lock_snapshots` / `lock_entries`: each SM12 upload is a snapshot. Lock age = snapshot time − lock time; when SM12 shows only a time, the most recent occurrence before the snapshot is used. Persistence counts how many snapshots contained the same user/table/argument/lock time.
- `sap_systems.timezone` converts SAP-local timestamps to UTC. Health adds jobs (warning on cancellations in 24 h or long-running jobs) and locks (warning ≥ 60 min, critical ≥ 240 min), ignoring data older than `OPS_DATA_STALE_HOURS`.

## UI

React 18 + TypeScript + Vite in `frontend/`. `npm run build` emits into `backend/app/static`; FastAPI serves assets and falls back to `index.html` for client-side routes (never for `/api/*`). The Docker image builds the UI in a Node stage. No UI component library; single-series charts are hand-built SVG with hover tooltips and a data-table fallback.

## Toolkit modules (v0.4)

- `services/tabular.py` – tolerant parser for SAP exports (delimiter, header alias, date and number locale detection).
- `services/alerts.py` – module-agnostic alert dispatch with de-duplication in `alert_events`.
- `services/cert_import.py` – STRUST/PSE certificate inventory from exported public certificates (private keys rejected).
- `services/growth.py` – snapshot storage, least-squares growth rates, disk/memory threshold forecast, housekeeping catalogue.
- `services/users.py` – stateless dormancy and license analysis; produces an SU10 lock list. No write access to SAP.
- `services/transports.py` – stateless E070/E071 pre-import risk analysis and import-log RC evaluation.

Data retention: growth snapshots and imported certificates are stored; user and transport uploads are analysed in memory only. Uploads are size-limited by `MAX_UPLOAD_BYTES`.

## Remaining production hardening

1. Tenant isolation, user identities/RBAC and audit trail.
2. Distributed scheduler locking if multiple scheduler replicas are required.
3. Centralized secrets management, TLS reverse proxy, network egress firewall and backup/restore runbooks.
4. SAP STRUST/PSE adapter and certificate-chain inventory.
5. Operational observability (metrics endpoint, structured logs).
6. Optional live collectors (RFC/OData) feeding the existing analysis services.
