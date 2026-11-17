# Initial architecture

## MVP boundary

The first increment is an endpoint-based TLS certificate monitor. It discovers the certificate presented by a configured HTTPS endpoint, records certificate metadata and evaluates expiry. It separately records whether the endpoint passes the machine's normal TLS trust and hostname validation.

This is not yet a STRUST/PSE inventory. Internal SAP PSE collection will be implemented as a separate adapter using supported SAP interfaces and a dedicated least-privilege technical user.

## Components

- **FastAPI API:** target registration, on-demand checks and check history.
- **Certificate service:** TLS handshake, X.509 parsing and trust validation.
- **SQLite development store:** zero-setup local persistence; production storage will move to PostgreSQL.
- **Future scheduler:** periodic checks and configurable alert windows.
- **Future SAP adapter:** STRUST/PSE metadata collection through a supported SAP integration mechanism.

## Security principles

- No SAP write access in the monitoring collector.
- Do not store private keys or certificate passwords.
- Treat endpoint names as operator-provided configuration.
- Apply network egress allowlists before exposing this service in production; otherwise target registration could be abused to probe internal services.
- Protect the API with authentication and authorization before production deployment.
- TLS inspection currently uses a bounded connection timeout.

## Next increments

1. Add endpoint validation and network egress controls.
2. Add scheduled checks and configurable 90/60/30/14/7-day alert thresholds.
3. Add notification adapters (email, Teams, Slack, generic webhook).
4. Add PostgreSQL support and migrations.
5. Implement SAP STRUST/PSE collector and landscape credentials vault integration.
6. Add multi-tenant RBAC, audit events and deployment hardening.
