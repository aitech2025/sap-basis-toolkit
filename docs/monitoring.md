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
- `COLLECTOR_TICK_SECONDS`, `COLLECTOR_MAX_PARALLEL`, `COLLECTOR_TIMEOUT_SECONDS`: control scheduling. See [scaling.md](scaling.md) for sizing a fleet of systems.

Collectors run in the `scheduler` container. API replicas never poll.

## 2. Sign in and roles

On first start, create the admin in the UI. First-run setup asks for the server API key (`API_AUTH_TOKEN`) as proof of ownership. Alternatively, set `ADMIN_PASSWORD` to bootstrap the admin at startup.

| Role | Can |
|---|---|
| viewer | Read every page |
| operator | Run checks and uploads, run collectors, acknowledge/resolve/assign incidents, schedule maintenance, edit systems, refresh instances and correct instance roles |
| admin | Everything, plus discover systems, start/stop/restart SAP systems and instances, configure collectors and credentials, edit alert rules, manage users, read the audit log |

Passwords are hashed with scrypt and must be at least 12 characters. Five failed sign-ins lock that user/IP for 15 minutes. Changing a password, role or active flag ends that user's sessions. The API key remains an admin service account for scripts and break-glass access. Every change is written to the audit log.

## 3. Discover a system from its host (recommended)

*Discover system* (admins) needs only a hostname or IP and, ideally, the `<sid>adm` OS login:

1. **Probe.** After the host passes the outbound policy, Sentinel connects to the sapstartsrv port of every instance number 00–99 in parallel (`5<NN>13`, or `5<NN>14` with HTTPS). You can enter one instance number to skip the sweep.
2. **Identify.** `GetInstanceProperties` returns the SID (`SAPSYSTEMNAME`) and instance name. `GetSystemInstanceList` returns every instance of the system on every host, with its features, start priority and status.
3. **Classify roles** by instance name, falling back to sapstartsrv features:

   | Instance name | Features | Role |
   |---|---|---|
   | `ASCS<NN>` / `SCS<NN>` | `MESSAGESERVER\|ENQUE` | ABAP / Java central services |
   | `ERS<NN>` | `ENQREP` | Enqueue replication server |
   | `DVEBMGS<NN>` | `ABAP` | Primary application server (PAS) |
   | `D<NN>` | `ABAP` | Additional application server (AAS); without a DVEBMGS instance, the lowest-numbered one is marked PAS (*inferred*) |
   | `J<NN>` | `J2EE` | Java server |
   | `W<NN>`, `SMDA<NN>`, `G<NN>`, `HDB<NN>` | `WEBDISP`, `SMDAGENT`, `GATEWAY`, `HDB` | Web Dispatcher, diagnostics agent, gateway, HANA |

   Roles can be corrected in the wizard or later on the *Instances & control* tab. Manual roles survive re-discovery.
4. **Product and database (best effort).** `ParameterValue(dbms/type)` gives the database (hdb, ora, db6, mss, ada, syb …). `ABAPGetComponentList` gives the software components: `S4CORE` → S/4HANA (108 = 2023), `DW4CORE` → BW/4HANA, `SAP_APPL` → ECC (618 = EHP8), `BBPCRM`, `SRM_SERVER`, `SCM`, `ST`, `GRCFND_A`, `BI_CONT`. Anything not readable is shown as a warning and can be chosen manually.
5. **Register.** The host is scanned again (the browser's instance list is never trusted). Sentinel then creates or updates the system, stores the instance inventory, creates the sapcontrol collector with the encrypted credentials (and optionally a HANA SQL collector), and runs the first collection. Each later collection keeps the inventory's status in sync and adds new instances.

The environment is suggested from the SID's last letter (P → PRD, Q/T → QAS, D → DEV). If the SID is already registered, you can update that system instead of creating a duplicate.

### OS login discovery (recommended): all three layers from one login

Choose **OS login (root or <sid>adm)** in *Discover system*. Enter one application server (the ASCS or PAS host) and its OS user and password, or a private key. Sentinel connects over SSH (port 22; see `SSH_ALLOWED_PORTS`) and runs only fixed, read-only commands. Nothing you type is passed to the shell.

| Layer | Read from |
|---|---|
| Application | `/usr/sap/sapservices` (every sapstartsrv and its profile: SID, instance name, number and host). Then the SAP Start Service of that instance for live state, roles, product and release. |
| Database | `DEFAULT.PFL` (`SAPDBHOST`, `dbms/type`, `dbs/hdb/dbname`, `dbs/hdb/schema`). On the database server: the HANA `manifest` (version), `saphostctrl -function ListDatabases` (as root) and the `/oracle`, `/db2`, `/sybase` and `/sapdb` directories. |
| Operating system | `/etc/os-release`, `uname`, CPU, memory, swap, load, uptime, virtualisation (`systemd-detect-virt`), time zone, IP addresses, and the usage of `/`, `/usr/sap`, `/sapmnt`, `/hana/*` and the database file systems. |

- The database server is reached with the same login unless you enter a different one. If it cannot be reached, the application and database layers are still recorded and a warning says how to add the OS layer.
- Each layer is stored as a discovery-sourced topology asset (application, database or hana, host), linked as follows: application *runs on* host, application *uses database* database, database *runs on* host.
- The new **Infrastructure** tab on the system page shows all three layers.
- One **OS inventory (SSH)** collector per server refreshes the OS facts every 15 minutes. The application host also feeds `host.memory_used_pct`, `host.load1` and `host.disk_used_pct` when no node_exporter collector exists.

**Security**
- SSH host keys are pinned on first use. A changed key stops collection until an admin sets *trust new host key* on the collector.
- Passwords and private keys are encrypted with `SECRET_KEY` and are never returned by the API.
- Only admins can run discovery.
- root sees everything. `<sid>adm` also works, but `saphostctrl ListDatabases` needs root.
- When you log in as root, enter the `<sid>adm` password as well to enable start/stop.

## 4. Start, stop and restart

The *Instances & control* tab can start, stop or restart the whole system or one instance through sapstartsrv:

- **System:** `StartSystem` / `StopSystem` / `RestartSystem` with `SAPControl-ALL-INSTANCES` (or only central services, application servers, ABAP or Java). sapstartsrv honours the start priorities: ASCS/ERS first on start and last on stop.
- **Instance:** `Start` / `Stop` / `RestartInstance` on that instance's own sapstartsrv. Stops accept a soft timeout so running work can finish.
- **Progress:** the calls are asynchronous. Sentinel polls `GetSystemInstanceList` every `CONTROL_POLL_SECONDS` until every targeted instance is GREEN (start/restart) or GRAY (stop), or until `CONTROL_TIMEOUT_SECONDS` (default 20 min). An instance turning RED fails the operation. A collection runs afterwards so the dashboards update immediately.
- **The database is not touched.** sapstartsrv controls SAP instances only. Start HANA or another database with SAP Host Agent or the database tools before starting the system.

Safeguards:

- Admin role only, plus the SID typed again and a reason. Every request is in the audit log and the operation history.
- Production (PRD) systems are **off by default**. An admin must set *Start / stop from Sentinel* to *Allowed* in the system settings. Any system can be set to *Not allowed*. `CONTROL_ENABLED=false` turns the feature off server-wide.
- One operation per system at a time. Operations still running when Sentinel restarts are marked *interrupted*.
- An optional maintenance window (default 30 min for stops) suppresses notifications for the expected outage.
- The collector's OS user needs permission for `Start`/`Stop` (`<sid>adm`, or `service/admin_users`). Discovery checks this with `AccessCheck` where the release supports it.

## 5. SAP Start Service collector (all ABAP and Java systems)

- **Endpoint:** the ASCS/SCS or any instance host, port `5<NN>13` (HTTP) or `5<NN>14` (HTTPS, recommended). The collector calls `GetSystemInstanceList` and then each instance's own sapstartsrv.
- **Credentials:** `GetSystemInstanceList` and `GetProcessList` are usually unprotected. `ABAPGetWPTable` and `ABAPReadSyslog` may be protected by `service/protectedwebmethods` (default `SDEFAULT`). In that case use `<sid>adm`, or an OS user/group listed in `service/admin_users` / `service/admin_groups`. Prefer a dedicated, least-privileged user where your release allows it.
- **System log:** if `ABAPReadSyslog` is unavailable on your release, the Errors tab shows that in the diagnostics. Upload SM21 exports instead.

## 6. HANA SQL collector (HANA-based systems)

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

## 7. Systems without a HANA collector (ECC on Oracle/Db2/MSSQL/…)

Use the SAP Start Service collector for availability, work processes and the system log. For errors and interfaces, upload SAP GUI exports on the Errors and Interfaces tabs: ST22, SM21, SM13, SM58, SMQ1/SMQ2 and WE02. The rules and incidents work the same way. Upload regularly, or from a script using the API key.

## 8. Alert rules, incidents and maintenance

- **Built-in rules:** global rules exist for every metric (see *Alert rules*). Admins can change thresholds or disable rules, and add landscape or system overrides. The most specific enabled rule wins.
- **Incident lifecycle:** a breach opens one incident per (metric, system). Repeats increase its count, a higher severity escalates it (and re-opens it if it was acknowledged), and recovery resolves it automatically. Operators acknowledge, assign, comment on and resolve incidents. Every step is on the timeline.
- **Module incidents:** alerts from certificates, background jobs, locks, transports and growth also open incidents. People resolve these manually.
- **Notifications:** sent on open, escalation and resolution through the configured webhook (generic, Slack or Teams), email and PagerDuty. PagerDuty receives critical incidents, plus a `resolve` event when they clear.
- **Maintenance windows:** cover one system, a landscape, or everything. Incidents are still recorded and flagged, but notifications are suppressed and the suppression is logged on the timeline.
- **Collector failures:** a collector that cannot reach its system opens a "Collector cannot reach the system" incident. Its data is then stale, and stale data is ignored for health.

## 9. Demo mode

`SIMULATOR_ENABLED=true` allows collectors with host `simulator` to produce synthetic, time-varying data for demos and tests. For discovery, use `simulator-<sid>`: `simulator-s4p` behaves like an S/4HANA 2023 system on HANA, `simulator-ecd` like ECC EHP8 on Oracle, `simulator-epq` like a Java system. Each has ASCS/SCS, ERS and two or three application servers. Simulated start/stop changes instance states over a few seconds. Never enable it in production.
