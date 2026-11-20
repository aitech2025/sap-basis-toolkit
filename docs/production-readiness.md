# Production deployment and operations

This runbook is the minimum deployment baseline for a customer-hosted SAP Basis Sentinel installation. It does not replace a customer security review, SAP release validation, or a recovery exercise.

## Deployment prerequisites

- A supported Linux host with Docker Engine and the Docker Compose plugin.
- PostgreSQL/TimescaleDB storage on persistent storage with monitored capacity.
- A TLS reverse proxy (for example, the customer's existing ingress or NGINX) in front of the API.
- Network routes from the scheduler to only the approved SAP application, HANA and OS monitoring endpoints.
- Outbound HTTPS from SAP application servers to the Sentinel ingress for the ABAP push agent.
- A tested backup destination outside the Sentinel host.

The Compose API port binds to 127.0.0.1:8000; it is not intended to be exposed directly to a network. Configure the reverse proxy to terminate TLS, enforce modern TLS settings, set request/body limits, and forward to 127.0.0.1:8000. Restrict the agent ingest route to the SAP network or a private ingress where possible.

## 1. Configure secrets and target policy

```bash
cp .env.example .env
openssl rand -hex 32  # use a fresh value for POSTGRES_PASSWORD
openssl rand -hex 32  # use a different value for API_AUTH_TOKEN
openssl rand -hex 32  # use a different value for SECRET_KEY
```

Set all three values independently in .env. Do not commit .env or place credentials in a source-controlled file. APP_ENV=production enables startup checks. Production startup fails when API/session secrets are weak or placeholders, SQLite is configured, the approved target host list is empty, or simulator mode is enabled.

Set ALLOWED_TARGET_HOSTS, ALLOWED_TARGET_CIDRS, and ALLOWED_TARGET_PORTS to the smallest possible set. Do not use wildcards covering an entire corporate domain unless that is explicitly accepted by the security team. Keep CONTROL_ENABLED=false initially. Lifecycle operations should be enabled only after a separate operational approval and system-level safeguards have been tested.

## 2. Start and verify

```bash
docker compose config -q
docker compose up -d --build
docker compose ps
docker compose logs --tail=200 api scheduler migrate
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ready
```

/health is a liveness check for the API process. /ready verifies that the database accepts a query. The API container healthcheck uses /ready; a running process with an unavailable database is not considered ready.

The migration service must complete successfully before API and scheduler startup. Do not run multiple migration jobs concurrently. Review the migration log and database schema version after every upgrade.

## 3. SAP connectivity and least privilege

- Prefer the outbound ABAP push agent or a customer-network connector. Do not expose SAP RFC ports to the public internet.
- Use a dedicated SAP technical user. Grant only the table and RFC permissions documented in abap/README.md; direct Open SQL reads require explicit authorization checks in the agent. Validate the checks with SU53/ST01 on the exact SAP_BASIS release and test both authorized and denied cases.
- For the HANA SQL collector, use a dedicated read-only HANA user and only the monitoring views and explicitly approved application tables required for enabled checks.
- Use HTTPS for the SM59 destination and validate the Sentinel server certificate through STRUST. Do not disable TLS checks.
- Pin SSH host keys and review collector diagnostics after first contact. Never silently accept changed host keys.
- Do not interpret a successful TCP probe as proof that SAP RFC logon, ABAP authorization or application health works.

The repository's ABAP lint job is a syntax/style check, not activation in an SAP system. Run the installation's collect-only mode first, inspect the SAP job log and Sentinel's per-area diagnostics, then run a controlled push test. A real SAP integration test remains mandatory before customer production use.

## 4. Backups and recovery

Back up PostgreSQL/TimescaleDB at least daily (more often if the customer's RPO requires it), retain encrypted copies outside the host, and test restores regularly.

Example logical backup:

```bash
umask 077
docker compose exec -T db pg_dump -U sentinel -d sentinel -Fc > sentinel-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Example restore into a newly created, empty database after stopping API and scheduler:

```bash
docker compose stop api scheduler
cat sentinel-backup.dump | docker compose exec -T db pg_restore -U sentinel -d sentinel --clean --if-exists
docker compose up -d api scheduler
```

Adapt restore options to the PostgreSQL version and the customer's database ownership policy. Test restore into an isolated environment first. A successful backup command is not proof of recoverability.

Back up the .env secret material separately in an approved secret manager. Loss or rotation of SECRET_KEY makes previously encrypted collector credentials unreadable; plan credential re-entry and rotation deliberately.

## 5. Monitoring the Sentinel service

Monitor at least:
- API /health and /ready availability and latency.
- Scheduler process health, collector backlog, missed intervals and failing collectors.
- PostgreSQL availability, disk capacity, connection saturation, replication/backup status if applicable.
- Agent last-seen time and agent-silent incidents.
- Notification delivery failures and audit-log retention.
- CPU, memory, filesystem usage and container restarts.

Alert on stale telemetry as well as threshold breaches. Missing data must remain visibly stale/unknown; it must not be treated as healthy or silently replaced with inferred values.

## 6. Upgrade and rollback procedure

1. Read release notes and migration changes; take and verify a backup.
2. Test the exact image and migration sequence in a staging environment using a production-like data volume.
3. Stop rollout if migration or health checks fail. Do not point an older application image at a schema it cannot support.
4. Deploy the new image, allow the single migration service to complete, then verify /ready, scheduler activity, and sample collectors before enabling user traffic.
5. Roll back the application image only when schema compatibility is confirmed. For destructive schema changes, restore the pre-upgrade backup instead of assuming a code rollback reverses the migration.

## 7. Release acceptance gates

A release is not production-approved until all of these have evidence:

- CI: backend tests, frontend production build, ABAP lint, fresh migration, Docker Compose validation and image build pass.
- Security: threat model, dependency and image scanning, secret handling, least-privilege roles, authorization denial tests, TLS verification, SSRF/egress policy and audit review are signed off.
- SAP: tested against supported SAP_BASIS releases with real sapcontrol, RFC/SDK where enabled, HANA and ABAP agent paths.
- Reliability: restart recovery, scheduler restart, collector timeout, duplicate delivery, DB outage and notification retry tests.
- Data: retention and downsampling verified at representative volumes; backups restored successfully.
- Scale: sustained load with realistic system/collector counts, telemetry rates, dashboard queries and concurrent users.
- Operations: runbooks, ownership, escalation, maintenance windows, support boundaries and customer RPO/RTO agreed.

Passing CI alone is not a production certification.
