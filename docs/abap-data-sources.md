# Where ABAP monitoring data comes from

## The problem with reading ABAP tables through HANA SQL

Until this change most ABAP signals (dumps, update errors, jobs, RFC queues, IDocs) came from SQL `SELECT`s on the ABAP
schema in HANA. That works, but it is the weakest of the available patterns:

- **HANA only.** ABAP systems on Oracle, Db2, ASE or MSSQL get nothing.
- **Not what SAP supports for monitoring.** It reads raw application tables around ABAP authorisations, and table layouts and
  semantics change between releases. For example, `SNAP.FLIST` is a packed field.
- **Blind to memory-resident state.** The SM12 lock table, dispatcher queues, current response times and CCMS values live in
  shared memory, not in the database.
- **Polling history, not state.** Each run re-reads tables (TBTCO, EDIDC) every 5 minutes.

## The patterns, compared

| Pattern | Latency | What it covers | Needs | Verdict |
|---|---|---|---|---|
| **sapstartsrv web methods** (sapcontrol, SOAP over HTTP 5NN13/14) | Live (shared memory) | Work processes (SM50), system log (SM21), SM12 lock table, enqueue statistics, dispatcher queues, instance CCMS tree: response time, users, dumps, update, background | `<sid>adm` (already used for availability). Some methods are protected; allow them in `service/protectedwebmethods` or use `<sid>adm` | **Now the primary live source** |
| **RFC with standard function modules** (SAP NW RFC SDK) | Seconds | Details the shared memory does not hold: ST22 dump list (`/SDF/GET_DUMP_LOG`, ST-PI), SM37 via XBP (`BAPI_XMI_LOGON` + `BAPI_XBP_JOB_SELECT`), ST03 workload (`SWNC_GET_WORKLOAD_SNAPSHOT`), servers and users (`TH_SERVER_LIST`, `TH_USER_LIST`) | RFC user with S_RFC for those modules, the NW RFC SDK (SAP download, not redistributable) and a binding. SAP's own Python binding, PyRFC, is archived and no longer maintained | The pattern commercial tools use. **Implemented: collector type `rfc`, see below** |
| **ABAP-side push** (an ABAP job or daemon that posts events to Sentinel) | Real time | Anything, as events | A transport into every SAP system | Most real-time. **Implemented: report `Z_SENTINEL_PUSH`, see below** |
| **ADT REST feeds** (`/sap/bc/adt/...` runtime errors feed) | Near real time | Short dumps | ADT authorisations, ICF service | Developer-oriented; no jobs or updates |
| **HANA SQL on ABAP tables** (current) | Polling | Dumps, updates, jobs, RFC, IDocs, transports, table growth | HANA, SELECT on the ABAP schema | **Kept as a fallback** for history and HANA-only details |

## What changed

The sapcontrol collector now also reads, on every run:

| Signal | Web method | Instance | Metrics |
|---|---|---|---|
| Dispatcher queues (DIA, UPD, BTC) | `GetQueueStatistic` | ABAP instances | `abap.queue_dia`, `abap.queue_upd`, `abap.queue_btc` |
| CCMS monitoring tree (current colours and values) | `GetAlertTree` | ABAP instances | `abap.dialog_response_ms`, `abap.frontend_response_ms`, `abap.users_logged_on`, `ccms.red_now`, `ccms.yellow_now` |
| Open CCMS alerts (context only) | `GetAlerts` | ABAP instances | none: alerts stay open until completed in RZ20, so they can be old |
| SM12 lock table | `EnqGetLockTable` | Enqueue instance (ASCS or central) | `abap.locks`, `abap.locks_oldest_min`, `abap.locks_long` |
| Lock table usage | `EnqGetStatistic` | Enqueue instance | `abap.enq_table_used_pct` |

- **Collection interval.** Newly discovered systems collect sapcontrol every 60 seconds; HANA SQL stays at 5 minutes and OS
  inventory at 15.
- **Locks.** SM12 locks feed the existing lock report without uploads. A lock's age comes from its owner ID, which starts with
  the creation time in the system's timezone, as SM12 shows it. When an owner ID has no timestamp, the lock gets the time it was
  first seen, so its age is a lower bound and never invented.
- **Lock snapshot storage.** A lock snapshot is stored every 5 minutes (`LOCK_SNAPSHOT_SECONDS`) and keeps locks held at least
  5 minutes (`LOCK_STORE_MIN_AGE_MINUTES`). The lock count always covers all locks.
- **New incident rules:**
  - dialog queue ≥ 10 / 50
  - update queue ≥ 20 / 100
  - lock table ≥ 70 / 90 %
  - dialog response ≥ 1,500 / 3,000 ms
  - any CCMS value red now (warning)
- **Where to see it.** The system's Availability tab has a new Live ABAP section.

### Authorisation

sapstartsrv protects some web methods. If a call fails with "Permission denied", either use `<sid>adm` as the collector user
(recommended) or remove the method from `service/protectedwebmethods` in the instance profile and restart sapstartsrv. Failed
calls appear in the collector's diagnostics; the rest of the run continues.

## RFC collector

Collector type `rfc` calls released or ST-PI function modules. It never uses `RFC_READ_TABLE`.

| Function module | Data |
|---|---|
| `RFC_SYSTEM_INFO` | SID, release, database (also guards against pointing the collector at the wrong system) |
| `TH_SERVER_LIST` | Application servers registered at the message server (`rfc.app_servers`) |
| `/SDF/GET_DUMP_LOG` (ST-PI) | ST22 dumps for the last 24 hours, with runtime error, user, server, client and program. The result table is generic; its columns are mapped from the description structure that the function returns |
| `BAPI_XMI_LOGON`, `BAPI_XBP_JOB_SELECT`, `BAPI_XMI_LOGOFF` | SM37 jobs of the last 7 days and running jobs, through the supported external job interface (XBP) |

**Setup**
- **Connection:** host of an application server, gateway port 33NN, the SAP client, and a system user.
- **User authorisations:** S_RFC for the modules above, plus S_XMI_PROD for XBP.

**Library**
- **Code:** written against the PyRFC client API.
- **Image build:** build with `RFC_CLIENT=true` after placing the SAP NW RFC SDK (downloaded from SAP; it may not be
  redistributed) in `backend/vendor/nwrfcsdk/`. `RFC_PACKAGE` selects the binding: SAP's archived `pyrfc` by default, or a
  drop-in replacement that provides the same `pyrfc` module.
- **Missing library:** without a library, the collector reports that clearly and the other collectors keep working.

## ABAP push agent

Report `Z_SENTINEL_PUSH` with class `ZCL_SENTINEL_AGENT` (folder `abap/`, abapGit format) runs as a periodic job in the SAP
system. It reads ST22, SM37, SM13, SM12, SM58, SMQ1/SMQ2 and IDoc errors with ABAP authorisations and POSTs them to
`/api/v1/agent/ingest` through an SM59 destination. This is the most real-time pattern, it works on every database, and it
needs no inbound connection into the SAP system.

- **Tokens:** one per system, issued and rotated by an admin on the Setup tab. Only a SHA-256 hash is stored, and the token
  is shown once.
- **What the endpoint checks:**
  - the token
  - that the payload's SID matches the system
  - the schema version
  - the size (`AGENT_MAX_BYTES`)
  - the push rate (`AGENT_MIN_INTERVAL_SECONDS`)
- **Audit log:** pushes are not written to it; the token records the last contact time, IP address and agent version.
- **Silence:** if an agent stops pushing for `AGENT_SILENT_SECONDS` (15 minutes), `agent.up` goes to 0 and an incident opens.
- **Same monitors from every source:** pushed data feeds the same dumps, updates, tRFC, qRFC, IDoc, job and lock monitors as
  HANA SQL and RFC. Each snapshot records its source.

Installation and authorisations: [`abap/README.md`](../abap/README.md).

## Which source to use

| You have | Use |
|---|---|
| `<sid>adm` for sapstartsrv | sapcontrol collector: availability, work processes, syslog, locks, queues, CCMS (always) |
| A transport path into the system | ABAP push agent: dumps, jobs, updates, RFC, IDocs and locks, near real time, any database |
| An RFC user but no transports | RFC collector: dumps and jobs through function modules |
| HANA and a SQL monitoring user | HANA SQL collector: HANA health, plus ABAP tables as a fallback and for table growth |
