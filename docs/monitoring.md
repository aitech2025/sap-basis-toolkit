# Live monitoring guide

SAP Basis Sentinel monitors every registered SAP system end to end:

| Area | Monitors | Source |
|---|---|---|
| Availability | System up/down, instances (ASCS/SCS, PAS/AAS), OS processes (dispatcher, ICM, gateway, message/enqueue server, jstart) | SAP Start Service (sapcontrol) |
| Work processes | DIA/BTC/UPD/SPO utilisation, long-running and stopped work processes, restarts | sapcontrol `ABAPGetWPTable` |
| Errors | ST22 short dumps, SM21 system log, SM13 update errors | HANA SQL (SNAP, VBHDR), sapcontrol `ABAPReadSyslog`, or uploads |
| Interfaces | SM58 tRFC errors, SMQ1/SMQ2 blocked queues, IDoc error statuses | HANA SQL (ARFCSSTATE, TRFCQOUT/TRFCQIN, EDIDC) or uploads |
| SAP HANA | Memory, CPU, data volume, statistics server alerts, last data/log backup, system replication, services | HANA SQL monitoring views |
| Operations | Background jobs, import history, table growth | HANA SQL (TBTCO, TPALOG, M_CS_TABLES) feeding the existing modules |

Every collector run stores a snapshot per monitor and metric samples. **Alert rules** turn metrics into **incidents**.

## 1. Configure the server

Set these in `.env` (see `.env.example`):

- `SECRET_KEY`: signs sessions and encrypts collector passwords. Generate it with `openssl rand -hex 32`. If you rotate it, collector passwords must be entered again.
- `ALLOWED_TARGET_HOSTS` / `ALLOWED_TARGET_CIDRS`: the SAP and HANA hosts the collectors may contact. Instance hostnames returned by `GetSystemInstanceList` must also be allowed.
- `HANA_CLIENT=true`: builds the image with SAP's `hdbcli` driver. It is distributed under the SAP Developer License; check that the licence suits your use.
- `COLLECTOR_TICK_SECONDS`, `COLLECTOR_MAX_PARALLEL`, `COLLECTOR_TIMEOUT_SECONDS`: control scheduling.

Collectors run in the `scheduler` container. API replicas never poll.

## 2. Sign in and roles

On first start, create the admin in the UI. First-run setup asks for the server API key (`API_AUTH_TOKEN`) as proof of ownership. Alternatively, set `ADMIN_PASSWORD` to bootstrap the admin at startup.

| Role | Can |
|---|---|
| viewer | Read every page |
| operator | Run checks and uploads, run collectors, acknowledge/resolve/assign incidents, schedule maintenance, edit systems |
| admin | Everything, plus configure collectors and credentials, edit alert rules, manage users, read the audit log |

Passwords are hashed with scrypt and must be at least 12 characters. Five failed sign-ins lock that user/IP for 15 minutes. Changing a password, role or active flag ends that user's sessions. The API key remains an admin service account for scripts and break-glass access. Every change is written to the audit log.

## 3. SAP Start Service collector (all ABAP and Java systems)

- **Endpoint:** the ASCS/SCS or any instance host, port `5<NN>13` (HTTP) or `5<NN>14` (HTTPS, recommended). The collector calls `GetSystemInstanceList` and then each instance's own sapstartsrv.
- **Credentials:** `GetSystemInstanceList` and `GetProcessList` are usually unprotected. `ABAPGetWPTable` and `ABAPReadSyslog` may be protected by `service/protectedwebmethods` (default `SDEFAULT`). In that case use `<sid>adm`, or an OS user/group listed in `service/admin_users` / `service/admin_groups`. Prefer a dedicated, least-privileged user where your release allows it.
- **System log:** if `ABAPReadSyslog` is unavailable on your release, the Errors tab shows that in the diagnostics. Upload SM21 exports instead.

## 4. HANA SQL collector (HANA-based systems)

Create a dedicated read-only user, for example:

```sql
CREATE USER SENTINEL_MON PASSWORD "<strong password>" NO FORCE_FIRST_PASSWORD_CHANGE;
GRANT MONITORING TO SENTINEL_MON;            -- monitoring views, _SYS_STATISTICS alerts, backup catalog
-- Optional ABAP runtime tables (replace SAPHANADB with your ABAP schema):
GRANT SELECT ON "SAPHANADB"."SNAP" TO SENTINEL_MON;
GRANT SELECT ON "SAPHANADB"."VBHDR" TO SENTINEL_MON;
GRANT SELECT ON "SAPHANADB"."ARFCSSTATE" TO SENTINEL_MON;
GRANT SELECT ON "SAPHANADB"."TRFCQOUT" TO SENTINEL_MON;
GRANT SELECT ON "SAPHANADB"."TRFCQIN" TO SENTINEL_MON;
GRANT SELECT ON "SAPHANADB"."EDIDC" TO SENTINEL_MON;
GRANT SELECT ON "SAPHANADB"."TBTCO" TO SENTINEL_MON;
GRANT SELECT ON "SAPHANADB"."TPALOG" TO SENTINEL_MON;
```

Validate the privileges on your HANA revision. If a query fails, it is listed in the collector diagnostics and the other monitors continue.

Reading ABAP tables directly bypasses ABAP authorisations. Grant exactly these SELECT privileges, to this user only. Port `3<NN>15` is the first tenant's SQL port.

### Query overrides

Some queries are marked "verify on your release" in the diagnostics: CPU from `M_LOAD_HISTORY_HOST`, update errors from `VBHDR`, inbound qRFC, and `TPALOG`. Replace any query, keeping the same output column names, with a JSON file referenced by `QUERY_OVERRIDES_FILE`:

```json
{"hana": {"updates": "SELECT VBKEY, VBUSR, VBTCODE, VBREPORT, VBDATE, VBRC FROM {schema}.VBHDR WHERE VBRC NOT IN (0)"}}
```

`{schema}` is replaced with the quoted ABAP schema. The query names are listed in `backend/app/services/collectors/hana.py`.

## 5. Systems without a HANA collector (ECC on Oracle/Db2/MSSQL/…)

Use the SAP Start Service collector for availability, work processes and the system log. For errors and interfaces, upload SAP GUI exports on the Errors and Interfaces tabs: ST22, SM21, SM13, SM58, SMQ1/SMQ2 and WE02. The rules and incidents work the same way. Upload regularly, or from a script using the API key.

## 6. Alert rules, incidents and maintenance

- **Built-in rules:** global rules exist for every metric (see *Alert rules*). Admins can change thresholds or disable rules, and add landscape or system overrides. The most specific enabled rule wins.
- **Incident lifecycle:** a breach opens one incident per (metric, system). Repeats increase its count, a higher severity escalates it (and re-opens it if it was acknowledged), and recovery resolves it automatically. Operators acknowledge, assign, comment on and resolve incidents. Every step is on the timeline.
- **Module incidents:** alerts from certificates, background jobs, locks, transports and growth also open incidents. People resolve these manually.
- **Notifications:** sent on open, escalation and resolution through the configured webhook (generic, Slack or Teams), email and PagerDuty. PagerDuty receives critical incidents, plus a `resolve` event when they clear.
- **Maintenance windows:** cover one system, a landscape, or everything. Incidents are still recorded and flagged, but notifications are suppressed and the suppression is logged on the timeline.
- **Collector failures:** a collector that cannot reach its system opens a "Collector cannot reach the system" incident. Its data is then stale, and stale data is ignored for health.

## 7. Demo mode

`SIMULATOR_ENABLED=true` allows collectors with host `simulator` to produce synthetic, time-varying data for demos and tests. Never enable it in production.
