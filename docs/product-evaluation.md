# Product idea evaluation

Scored 1–5 (5 = best). "Risk" is the security and operational risk of running the tool against production (5 = lowest risk).

| # | Idea | Customer pain | Ease to build | Risk | Verdict |
|---|------|---------------|---------------|------|---------|
| 1 | Pre/post transport checker | 5 | 3 | 5 (read-only) | **Built (v0.4)**, export-based |
| 2 | SSL certificate & PSE expiry monitor | 5 | 5 | 5 | **Already built; extended** |
| 3 | HANA & table growth watchdog | 4 | 4 | 5 (read-only) | **Built (v0.4)** |
| 4 | Inactive user & license optimizer | 5 (direct cost savings) | 4 | 4 (recommend only) | **Built (v0.4)**, locking stays manual |
| 5 | Self-service password reset/unlock bot | 3 | 2 | 1 | **Deferred** |
| 6 | System refresh post-copy automator | 4 | 1 | 2 | **Phase 3**: start with export/diff only |

## Assessment

**1. Transport checker – build.** Failed or out-of-sequence imports are a weekly pain, and the useful checks need only E070/E071 data: object collisions, an older version imported after a newer one ("sequence inversion"), predecessors that were never imported, unreleased requests, deletions, DDIC table changes that may need SE14 conversion, and table content for protected tables (T000, USR*, RFCDES, TVARVC…) without approval. Post-import, RC ≥ 8 from STMS history or tp logs raises an alert. True cross-reference dependency analysis (where-used across packages) needs a live RFC and is a later phase.

**2. Certificate monitor – already the strongest MVP; filled the gaps.** The existing endpoint monitor was solid but posted only to a generic webhook and could not see certificates inside STRUST. Added: Slack, Teams (Workflows/Adaptive Card) and PagerDuty formats; re-enabling a target; a summary endpoint; and a STRUST/PSE inventory from exported certificates, which covers client PSEs and SAML/WS-Security certificates that endpoint scanning cannot see.

**3. Growth watchdog – build.** Simple to deliver from periodic DB02/HANA exports: least-squares growth per table, days until disk/memory reaches 90%, and a catalogue of standard housekeeping for the usual suspects (BALDAT/BALHDR → SBAL_DELETE, IDocs → archiving object IDOC, DBTABLOG → RSTBPDEL, spool → RSPO1041, job logs → RSBTCDEL2, change pointers → RBDCPCLR2…). "Automated cleanup" is deliberately **recommend-only**: deleting application data in production needs functional sign-off and retention rules.

**4. User & license optimizer – build; highest measurable ROI.** Dormant dialog users and over-licensed users show up directly as licence savings. The analysis excludes system, communication, service and reference users, protected technical users and recent hires, then produces an SU10 lock list and downgrade candidates, with an indicative FUE estimate (1 / 0.2 / 0.033). **Automatic locking was not built**: it needs a write-capable RFC user in production and should stay a reviewed admin action (SOX/ITGC). The FUE weights and the classification of transactions must be checked against each customer's contract.

**5. Password reset/unlock bot – defer.** It needs MFA/SSO identity proofing, a privileged user-admin RFC user exposed to a chat channel (a high-value attack path), and audit evidence for every reset. SAP Identity Authentication, SAP IAG and existing ITSM password tools already cover this. Revisit only as an integration with the customer's existing identity provider, never as a standalone bot.

**6. Post-copy automator – phase 3.** It is the most valuable remaining idea, but also the riskiest and most release-specific (BDLS runtime, RFC destinations, SCC4/SE06 settings, printers, partner profiles, background jobs, STMS). A safe first step that fits this toolkit is **export before / diff after**: store a pre-copy snapshot of RFCDES, TBDLS, EDPP1/EDP13 and TSP03 exports and report what the copy overwrote, plus a tracked checklist. Full automation needs a release-validated RFC/ABAP add-on.

## What shipped in v0.4

- Slack / Teams / PagerDuty alert formats (PagerDuty receives critical/error only for toolkit modules).
- `PATCH /targets/{id}/enable`, `GET /certificates/summary`.
- STRUST/PSE certificate import and daily threshold alerts (private keys rejected).
- Table growth watchdog with capacity forecast, housekeeping advice and weekly de-duplicated alerts.
- User & license optimizer (stateless; CSV lock list export).
- Transport pre-check and post-check with RC ≥ 8 alerting.
- Dashboard at `/`, sample exports in `samples/`, Alembic revision `20261117_0002`.
- Fix: certificate e-mail bodies previously contained literal `\n` instead of line breaks.

## Suggested roadmap

1. **Live collectors** (optional): read-only RFC user feeding the same services (RFC_READ_TABLE replacements or a small Z function module per data set), scheduled snapshots.
2. **Post-copy phase 1**: pre/post snapshot diff + refresh checklist.
3. **Multi-tenant / RBAC** for MSP use (see architecture hardening list).
4. **Transport where-used analysis** via live cross-reference once collectors exist.
