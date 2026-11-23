*&---------------------------------------------------------------------*
*& Report Z_SENTINEL_PUSH
*&---------------------------------------------------------------------*
*& SAP Basis Sentinel push agent. Schedule in SM36 as a periodic job
*& (every 1-5 minutes) with a variant holding the destination and the
*& agent token issued in Sentinel (system > Setup > ABAP push agent).
*& Read-only: collects ST22, SM37, SM13, SM12, SM58, SMQ1/SMQ2, IDoc,
*& SNRO, SLG1, SM35, SCC4, SU01 (standard users), SP01 and STMS signals
*& and posts them to Sentinel. A failed push ends the job with
*& an error so it is visible in SM37.
*&---------------------------------------------------------------------*
REPORT z_sentinel_push.

PARAMETERS:
  p_dest  TYPE rfcdest DEFAULT 'SENTINEL' OBLIGATORY,
  p_token TYPE c LENGTH 64 LOWER CASE OBLIGATORY,
  p_rows  TYPE i DEFAULT 2000,
  p_test  AS CHECKBOX DEFAULT space.

START-OF-SELECTION.
  DATA(go_agent) = NEW zcl_sentinel_agent( iv_max_rows = p_rows ).
  DATA(gs_payload) = go_agent->collect( ).
  DATA gv_status TYPE i.
  DATA gv_response TYPE string.

  WRITE: / 'Sentinel agent', zcl_sentinel_agent=>c_version, 'SID', gs_payload-sid, 'client', gs_payload-client.
  WRITE: / 'Dumps:', lines( gs_payload-dumps ), 'source', gs_payload-dump_source,
         / 'Jobs:', lines( gs_payload-jobs ), 'Update errors:', lines( gs_payload-updates ),
         / 'Locks:', lines( gs_payload-locks ), 'tRFC errors:', lines( gs_payload-trfc ),
         / 'qRFC queues out/in:', lines( gs_payload-qrfc_out ), lines( gs_payload-qrfc_in ),
         / 'IDoc errors:', lines( gs_payload-idoc_errors ), 'of', gs_payload-idoc_total,
         / 'Number ranges >= 50% used:', lines( gs_payload-number_ranges ),
         / 'Application log objects with errors:', lines( gs_payload-applog_errors ),
         / 'Batch input sessions in error:', lines( gs_payload-batch_input ),
         / 'Clients:', lines( gs_payload-clients ), 'Standard users:', lines( gs_payload-critical_users ),
         / 'Spool requests:', gs_payload-spool_requests, 'Imports since yesterday:', lines( gs_payload-imports ).
  LOOP AT gs_payload-errors INTO DATA(gv_error).
    WRITE: / 'Not collected:', gv_error.
  ENDLOOP.

  IF p_test = abap_true.
    WRITE: / 'Test mode: nothing sent.'.
    RETURN.
  ENDIF.

  go_agent->push(
    EXPORTING
      iv_destination = p_dest
      iv_token       = CONV string( p_token )
      is_payload     = gs_payload
    IMPORTING
      ev_status      = gv_status
      ev_response    = gv_response ).

  WRITE: / 'HTTP status', gv_status, gv_response.
  IF gv_status < 200 OR gv_status >= 300.
    MESSAGE |Sentinel push failed: { gv_status } { gv_response }| TYPE 'E'.
  ENDIF.
