# SAP Basis Sentinel – certificate monitoring MVP

## Delivered

- HTTPS endpoint certificate discovery and X.509 metadata extraction.
- Separate trust and hostname validation.
- On-demand and scheduled checks.
- Configurable expiry thresholds (default 90/60/30/14/7 days).
- Generic JSON webhook notifications (Slack/Teams incoming webhooks) and SMTP email.
- Notification event history and successful-alert de-duplication per certificate, threshold and channel; failed deliveries are retried on later checks.
- SQLAlchemy storage supporting SQLite for local development and PostgreSQL for shared deployments.
- Read-only design; no private keys or certificate secrets are stored.

## API

- GET /health
- POST /api/v1/targets
- GET /api/v1/targets
- POST /api/v1/targets/{target_id}/check
- GET /api/v1/checks?limit=100
- GET /api/v1/notifications?limit=100

## Configuration

Copy backend/.env.example and set environment variables in the process environment. PostgreSQL URL example: postgresql+psycopg://user:password@host:5432/database.

The scheduler runs every CHECK_INTERVAL_MINUTES (default 360). It performs its first sweep after the first interval; use the API for an immediate check after registering a target.

## SAP STRUST/PSE integration boundary

Endpoint TLS discovery only sees certificates presented during a network handshake. It cannot inventory all certificates stored in STRUST, including client PSEs and certificates not currently presented by an endpoint.

A typed SAP PSE collector contract is included, but the RFC implementation is deliberately not enabled. It must be built and tested against a supported, release-specific SAP interface with a dedicated least-privilege RFC identity. Do not read or modify STRUST filesystem files directly, guess internal table layouts, or use unrestricted RFC_READ_TABLE as a production integration contract. Until tested against a real SAP system, inventory must be labelled endpoint-only.

## Operational and security notes

- Deploy inside the customer network to reach private SAP endpoints.
- Restrict API access at the reverse proxy/network layer until authentication and RBAC are added.
- Restrict outbound network access to approved target hosts and ports; target registration can otherwise be abused as an SSRF/network-probing primitive.
- Store SMTP credentials and webhook URLs in a secrets manager or deployment environment, never source control.
- Use PostgreSQL for multi-instance/production deployments. SQLite is for local development only.
- Multiple API worker processes each starting an in-process scheduler would create duplicate sweeps. Run one scheduler instance (or a dedicated worker) in production.

## Next

1. API authentication, tenant isolation and audit trail.
2. Dedicated scheduler worker and retry/backoff policy.
3. Implement and validate the SAP STRUST/PSE adapter for target SAP_BASIS releases.
4. Certificate chain inventory and endpoint discovery.
5. Dashboard and deployment packaging.
