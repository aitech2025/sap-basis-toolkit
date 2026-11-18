# Collector audit: application, database and OS layers

Audit date: 18 November 2026 (branch `feature/os-discovery`).

## Coverage matrix

| Layer | What is collected | Collector | Transport | Metrics that alert |
|---|---|---|---|---|
| Application | Instance availability, process list per instance (ASCS/ERS/PAS/AAS, Java) | `sapcontrol` | sapstartsrv SOAP over HTTP(S), Basic auth as `<sid>adm` | `availability.system_up`, `availability.instances_down` |
| Application | Work processes (SM50), system log (SM21) | `sapcontrol` | `ABAPGetWPTable`, `ABAPReadSyslog` | `wp.dia_busy_pct`, `wp.btc_busy_pct`, `wp.long_running`, `wp.stopped` |
| Application (via DB) | Short dumps, update errors, tRFC/qRFC, IDocs, jobs, imports, table growth | `hana` | SQL (hdbcli) on the ABAP schema | `dumps.*`, `updates.*`, `trfc.*`, `qrfc.*`, `idocs.*` |
| Database | HANA memory, CPU, data volume, alerts, backups, replication, services, statements, threads | `hana` | SQL (hdbcli), read-only monitoring user | `hana.*`, including the new `hana.services_inactive` |
| Database | HANA instance availability from its own sapstartsrv | `hana_sapcontrol` (optional) | SOAP, needs `<dbsid>adm` | `hana.system_up`, `hana.instances_down` |
| Database | Database process running on the DB host (HANA, Oracle, Db2, ASE, MSSQL) | `os_ssh` on the DB host | SSH | `db.os_process_up` |
| OS (application host) | Memory, load, disk (`/`, `/usr/sap`, `/sapmnt`) | `node_exporter`, or `os_ssh` when no node_exporter exists | HTTP(S) scrape / SSH | `host.memory_used_pct`, `host.disk_used_pct`, `host.load1` |
| OS (database host) | Memory, load, disk (`/hana/*`, `/oracle`, `/db2`, `/sybase`, `/sapdb`) | `os_ssh` on the DB host | SSH | `db.host_memory_used_pct`, `db.host_disk_used_pct`, `db.host_load1` |
| OS (both) | OS release, kernel, CPU, memory size, file systems, virtualisation, SAP and DB processes | `os_ssh` | SSH (root or `<sid>adm`) | inventory (Infrastructure tab) |

Discovery with an OS login creates `sapcontrol`, `os_ssh` for every host, and `hana` when HANA credentials are given.
Without HANA credentials the database layer is still covered by the DB host's OS metrics and the process check.

## How it was verified

- **Simulator matrix** (`tests/test_os_discovery.py::test_collector_matrix_covers_all_three_layers`, runs in CI): registers a
  system through OS discovery, runs every collector and asserts that each layer's metrics arrive and that every emitted metric
  is in the analytics catalogue.
- **Lab against real services** (`tests/test_lab_collectors.py`, opt-in with `SENTINEL_LAB=1` after `tests/lab/setup_lab.sh`):
  - real OpenSSH with `root` and a `<sid>adm` user whose login shell is tcsh (as SAP installs it), password and key authentication,
    host-key pinning and changed-key refusal, wrong password, and a database process that stops;
  - the real Prometheus node_exporter 1.8.2 binary;
  - sapcontrol over real HTTP against a sapstartsrv stand-in that sends sapstartsrv's SOAP documents (namespaces, item lists,
    SOAP faults, HTTP 401 without valid `<sid>adm` credentials), with separate ports per instance (5NN13);
  - HANA SQL through the real `HanaClient` with a stand-in for SAP's hdbcli driver (connect options, cursor protocol, login failure).

## Defects found and fixed

1. **SSH failed for `<sid>adm` users.** SAP creates `<sid>adm` with csh/tcsh, which cannot parse the `sh -c '<script>'` form. The
   script is now sent on stdin to `env LC_ALL=C /bin/sh -s`, which works under any login shell (verified with tcsh).
2. **Host metrics never alerted.** There were no incident rules for `host.*`. Added memory (90/97 %) and disk (85/95 %) rules.
3. **No database check for non-HANA databases** and none without HANA SQL credentials. `os_ssh` on the DB host now checks the
   database's main process (`hdbindexserver`, `ora_pmon_*`, `db2sysc`, `dataserver`, `sqlservr`) and reports DB-host OS metrics.
4. **Single-host systems lost the database check.** An `os_ssh` collector added by hand, or run as a connection test, assumed the
   application role. The role now comes from the host itself (instances in `/usr/sap/sapservices` and running processes) and is
   merged with the role discovery recorded. Roles are only added, never removed, so a stopped database still raises an alert.
5. **Zombie processes counted as running.** A defunct database process still appears in `ps`. Z-state processes are now excluded.
6. **Wrong HANA SQL.** `M_SERVICE_THREADS.THREAD_STATE` has no value `WAITING` (states are e.g. `Running`, `Semaphore Wait`), and
   `M_ACTIVE_STATEMENTS` has no `DURATION_MICROSEC`. Both queries were rewritten.
7. **Inactive HANA services did not alert.** They turned the monitor red but produced no metric. Added `hana.services_inactive` with
   a critical rule.
8. **Two HANA metrics could not be charted** (`hana.max_statement_duration_s`, `hana.expensive_statement_max_duration_s`): they were
   missing from the analytics catalogue.

## Known limits

- The HANA SQL collector needs SAP's `hdbcli` driver, which is not installed by default (`HANA_CLIENT=true` at image build). The
  lab test exercises our client code with a stand-in driver, not a live HANA database.
- The sapcontrol test uses a stand-in that follows sapstartsrv's documented responses; validate the first connection to a real
  system with the collector's **Test connection** button.
- Java systems get availability and processes only (there is no work-process or syslog equivalent in sapcontrol for AS Java).
- `host.*` metrics have no host dimension, so the database host reports under `db.host_*` names. Systems with several
  application servers report the PAS host only (or node_exporter's target).
- Non-HANA databases have process and OS checks but no SQL-level monitoring (no Oracle/Db2/ASE/MSSQL SQL collector yet).
