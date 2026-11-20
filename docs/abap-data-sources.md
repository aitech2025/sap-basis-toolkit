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
| **RFC with standard function modules** (SAP NW RFC SDK) | Seconds | Details the shared memory does not hold: ST22 dump list (`/SDF/GET_DUMP_LOG`, ST-PI), SM37 via XBP (`BAPI_XMI_LOGON` + `BAPI_XBP_JOB_SELECT`), ST03 workload (`SWNC_GET_WORKLOAD_SNAPSHOT`), servers and users (`TH_SERVER_LIST`, `TH_USER_LIST`) | RFC user with S_RFC for those modules, the NW RFC SDK (SAP download, not redistributable) and a binding. SAP's own Python binding, PyRFC, is archived and no longer maintained | The pattern commercial tools use (Dynatrace: RFC; Avantra: agent). **Recommended next step, see below** |
| **ABAP-side push** (an ABAP job or daemon that posts events to Sentinel) | Real time | Anything, as events | A transport into every SAP system | Most real-time, most invasive. Not planned |
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

## Recommended next step: an RFC collector

For ST22 details (program, include, user, transaction for each dump) and SM37 through the supported XBP interface, add an
optional RFC collector:

- **Function modules:** `/SDF/GET_DUMP_LOG`, `BAPI_XMI_LOGON` and `BAPI_XBP_JOB_SELECT`, `SWNC_GET_WORKLOAD_SNAPSHOT`,
  `TH_SERVER_LIST`.
- **Deployment:** as optional as the HANA client, built into the image only when the customer provides the SAP NW RFC SDK.
- **Binding:** this is the decision to make.
  - PyRFC 3.3.1 works, but SAP no longer maintains it.
  - A community replacement implements the RFC protocol without the SDK, but it is young.
  - A small Java JCo sidecar uses SAP's supported connector, at the cost of another runtime.

When it is in place, the HANA SQL reads of SNAP and TBTCO can be retired for systems that have RFC.
