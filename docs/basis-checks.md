# Basis daily checks

The **Daily checks** page covers the classic Basis morning checklist for every system on one screen.
- **Thresholds:** each cell is classified with the same thresholds as alerting. The system's effective alert rule is used,
  and the defaults below apply only where no rule exists.
- **Details:** hover a cell for the value, the threshold and the transaction to check in SAP.
- **Sign off:** marks the checks as reviewed. The results at that moment, the reviewer and the comment are stored, which
  gives an audit trail of the daily check.
- **Export:** the same matrix downloads as CSV.

A value older than the check's freshness window is shown as **stale**, never as green, so a collector that stopped never
looks healthy. An empty cell means the signal is not collected for that system: for example, HANA checks on an Oracle
system, or security checks without the ABAP push agent.

## The checklist and where each value comes from

| Area | Check | Transaction | Source | Default warning / critical |
|---|---|---|---|---|
| Availability | System available, instances down | SM51, SMMS | sapcontrol | down / - |
| | HANA instance up | | sapcontrol of HDB | down |
| | Database process running | ST06 | SSH (DB host) | down |
| | ABAP agent reporting | SM37 | push agent | silent > 15 min |
| Work processes | Dialog WPs busy, long-running WPs | SM50, SM66 | sapcontrol | 80 / 95 %, 1 |
| | Dialog queue | dpmon | sapcontrol | 10 / 50 |
| Performance | Dialog response time | ST03 | sapcontrol (CCMS) | 1,500 / 3,000 ms |
| | Buffer swaps, lowest hit ratio | ST02 | sapcontrol (CCMS) | 1,000 / 10,000 swaps |
| Errors | Short dumps (24 h) | ST22 | push agent, RFC, HANA SQL | 50 / 200 |
| | System log errors (1 h) | SM21 | sapcontrol | 10 / 50 |
| | Update errors | SM13 | push agent, HANA SQL | 1 |
| | Application log errors (24 h) | SLG1 | push agent, HANA SQL | 100 |
| Background jobs | Cancelled jobs (24 h) | SM37 | push agent, RFC (XBP), HANA SQL | 1 / 10 |
| Locks | Oldest lock, lock table usage | SM12 | sapcontrol, push agent | 60 / 240 min, 70 / 90 % |
| Interfaces | tRFC errors | SM58 | push agent, HANA SQL | 1 / 50 |
| | Blocked qRFC queues | SMQ1, SMQ2 | push agent, HANA SQL | 1 / 5 |
| | IDoc errors (24 h) | WE02, BD87 | push agent, HANA SQL | 1 / 50 |
| | Batch input sessions in error (7 days) | SM35 | push agent, HANA SQL | 1 |
| Capacity | Number range fill level | SNRO | push agent, HANA SQL | 80 / 90 % |
| Database | HANA memory, data volume, log volume | HANA cockpit, DB02 | HANA SQL | 85 / 95 %, 80 / 90 % (log) |
| | Last data / log backup | DB12 | HANA SQL | 26 / 50 h, 1 / 4 h |
| | System replication, high alerts | | HANA SQL | |
| | Blocked transactions, out-of-memory events | | HANA SQL | 5 / 20, 1 |
| Operating system | CPU, memory, swap, file systems (app and DB host) | ST06 | SSH or node_exporter | 85 / 95 % CPU, 20 / 50 % swap |
| Security | Production client open for changes | SCC4 | push agent (HANA SQL reads T000 too) | 1 (production, pre-production and QA only) |
| | SAP* missing or unlocked | SU01 | push agent | 1 |
| | Certificate expiry | STRUST, SMICM | TLS checks, STRUST import | 30 / 7 days |

**Notes on individual checks**
- **CPU:** measured over one second from `/proc/stat` (SSH) or from the rate of `node_cpu_seconds_total` between two scrapes
  (node_exporter).
- **Number ranges:** only internal ranges are evaluated; the application numbers external ranges itself.
- **SAP\*:** best practice is a SAP* user that exists and is locked in every client. Without a SAP* record, the kernel
  accepts the default password unless `login/no_automatic_user_sapstar = 1`.
- **USR02 is never read over SQL:** granting a database user access to USR02 would expose password hashes. Standard-user
  checks come only from the ABAP push agent, which reads inside SAP under ABAP authorisations.

## Database

- `metric_latest`: the newest value per system and metric. It is updated on every write and never moves back when an older
  value arrives late. The daily checks, and other fleet views, read this table instead of the time series.
- `daily_check_reviews`: one row per sign-off (date, landscape, reviewer, comment, summary and the full matrix as reviewed).
- **Migration:** `20261118_0014` creates both tables and fills `metric_latest` from the last 7 days of `metric_points`.

## API

| Method | Path | Role |
|---|---|---|
| GET | `/api/v1/daily-checks?landscape=&environment=` | viewer |
| GET | `/api/v1/daily-checks/export.csv` | viewer |
| POST | `/api/v1/daily-checks/reviews` `{landscape, comment}` | operator |
| GET | `/api/v1/daily-checks/reviews?days=30`, `/reviews/{id}` | viewer |
