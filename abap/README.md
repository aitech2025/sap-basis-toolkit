# SAP Basis Sentinel: ABAP push agent

The agent is a report and a class that run as a periodic background job inside each SAP system. Each run reads the system's
ABAP runtime state with ABAP authorisations and posts one JSON document to Sentinel.

| Object | Type | Purpose |
|---|---|---|
| `ZCL_SENTINEL_AGENT` | Class | Collects the data (read-only) and posts it through an SM59 HTTP destination |
| `Z_SENTINEL_PUSH` | Report | Job step: collect, show what was collected, push; a failed push ends the job with an error |

## What it reads

Each run covers a rolling window from yesterday at the same time until now. Sentinel de-duplicates repeated rows, so a lost
push loses nothing.

| Area | Source | Transaction |
|---|---|---|
| Short dumps | `/SDF/GET_DUMP_LOG` (ST-PI); without ST-PI the SNAP headers | ST22 |
| Background jobs | TBTCO (started since yesterday, plus running) | SM37 |
| Update errors | VBHDR (`VBRC <> 0`) | SM13 |
| Locks | `ENQUEUE_READ` | SM12 |
| tRFC errors | ARFCSSTATE (SYSFAIL, CPICERR, ...), all clients | SM58 |
| qRFC queues | TRFCQOUT / TRFCQIN by queue and state, all clients | SMQ1 / SMQ2 |
| IDoc errors | EDIDC error statuses since yesterday, all clients | WE02 |
| Number ranges | NRIV internal ranges at least 50% used, all clients | SNRO |
| Application log errors | BALHDR logs with abort/error messages since yesterday, by object | SLG1 |
| Batch input errors | APQI sessions in error, last 7 days | SM35 |
| Client settings | T000 change options per client | SCC4 |
| Standard users | USR02 lock state of SAP*, DDIC, TMSADM, EARLYWATCH, SAPCPIC; users locked by failed logons | SU01 |
| Spool | number of spool requests (TSP01) | SP01 |
| Transports | TPALOG imports since yesterday with return codes | STMS |

Areas that cannot be read (for example, a missing authorisation) are listed in the job log and in the payload; the other areas
are still sent. The collector explicitly runs `AUTHORITY-CHECK` for `S_TABU_NAM` before each direct Open SQL table read and
for `S_RFC` before calling RFC-enabled function modules. Open SQL does **not** enforce `S_TABU_NAM` automatically. Granting
table access in a role alone is not enough unless the role grants the exact activity and table values checked here.

## Installation

1. **Import.** Use abapGit (online from this folder, or offline with the ZIP from Sentinel: system > Setup > ABAP push agent >
   Download ABAP sources) into a Z package, for example `ZSENTINEL`, and activate.
2. **Token.** In Sentinel, open the system's Setup tab and issue an agent token. It is shown once.
3. **Destination.** In SM59, create HTTP destination `SENTINEL` (type G):
   - host: Sentinel's host name, port 443
   - path prefix: `/api/v1/agent/ingest`
   - SSL: active; import the Sentinel server certificate (or its CA) into the SSL client PSE in STRUST
4. **Test.** Run `Z_SENTINEL_PUSH` with *Collect only* to see what can be read, then without it to check the push. The job log
   shows the HTTP status.
5. **Variant.** Save a variant with the destination and the token. Protect it: anyone who can read variants of this report can
   see the token.
6. **Schedule.** In SM36, schedule `Z_SENTINEL_PUSH` with the variant as a periodic job every 1 to 5 minutes, under a system
   user (type B).

## Authorisations for the job user

- `S_TABU_NAM`, activity `03` (display), for each table actually collected: `TTZCU`, `SNAP` (fallback only), `TBTCO`, `VBHDR`,
  `ARFCSSTATE`, `TRFCQOUT`, `TRFCQIN`, `EDIDC`, `NRIV`, `BALHDR`, `APQI`, `T000`, `USR02`, `TSP01`, and `TPALOG`.
- `S_RFC`, type `F`, activity `16` (execute), for `ENQUEUE_READ` and `/SDF/GET_DUMP_LOG` (function group `/SDF/SMD_E2E`).
- `S_ENQUE` display where required by the target release for lock-list access.
- `S_ICF`: use of the SM59 destination, if destinations are protected

## Security notes

- Do not use `S_TABU_DIS` as a substitute for the explicit `S_TABU_NAM` checks in this release of the agent. If your security
  model only grants table authorization groups, implement and validate a separate `S_TABU_DIS` check against the correct
  authorization group for each table before enabling collection.
- Use a dedicated background user and restrict report variants/job administration. The destination token is stored in the
  report variant and can be read by users who can display that variant.
- Configure SM59 with HTTPS certificate validation. Never disable TLS verification to make the push work.
- Validate every table and function authorization in SU53/ST01 on the target SAP_BASIS release before scheduling the job.

## Compatibility

- Written in ABAP 7.40 SP08 syntax and checked with abaplint for 7.40 SP08 and 7.50 (`abaplint.json`).
- Standard tables are read with `INTO CORRESPONDING FIELDS`, so a field missing on an older release stays empty instead of
  breaking activation.
- `/SDF/GET_DUMP_LOG` is called dynamically: the class activates without ST-PI and falls back to SNAP.
- T000 and NRIV are read with dynamic SQL across clients, falling back to a plain read where a table is cross-client. The
  BALHDR condition is dynamic too, so a release without its message counters reports an error instead of failing activation.
- abaplint cannot see SAP's dictionary, so table-specific checks (for example, which tables are client-dependent) happen at
  activation in your system.
