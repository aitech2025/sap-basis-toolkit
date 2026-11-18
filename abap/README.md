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
are still sent.

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

- `S_TABU_NAM` (or `S_TABU_DIS`), display: SNAP, TBTCO, VBHDR, ARFCSSTATE, TRFCQOUT, TRFCQIN, EDIDC, TTZCU, NRIV, BALHDR,
  APQI, T000, USR02 (only the fields BNAME, UFLAG, LOCNT, TRDAT and USTYP are read), TSP01, TPALOG
- `S_RFC`: function groups of `/SDF/GET_DUMP_LOG` (`/SDF/SMD_E2E`) and `ENQUEUE_READ`; `S_ENQUE` display
- `S_ICF`: use of the SM59 destination, if destinations are protected

## Compatibility

- Written in ABAP 7.40 SP08 syntax and checked with abaplint for 7.40 SP08 and 7.50 (`abaplint.json`).
- Standard tables are read with `INTO CORRESPONDING FIELDS`, so a field missing on an older release stays empty instead of
  breaking activation.
- `/SDF/GET_DUMP_LOG` is called dynamically: the class activates without ST-PI and falls back to SNAP.
- T000 and NRIV are read with dynamic SQL across clients, falling back to a plain read where a table is cross-client. The
  BALHDR condition is dynamic too, so a release without its message counters reports an error instead of failing activation.
- abaplint cannot see SAP's dictionary, so table-specific checks (for example, which tables are client-dependent) happen at
  activation in your system.
