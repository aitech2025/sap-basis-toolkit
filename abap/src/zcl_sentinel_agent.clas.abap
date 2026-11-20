"! <p class="shorttext synchronized">SAP Basis Sentinel push agent</p>
"! Collects ABAP runtime signals inside the system (with the authorisations of the job user) and posts them as JSON
"! to Sentinel through an SM59 HTTP destination (type G). Read-only: it never changes data in this system.
"! Every area is collected independently; a failing area is reported in ERRORS and the others are still sent.
CLASS zcl_sentinel_agent DEFINITION
  PUBLIC
  FINAL
  CREATE PUBLIC.

  PUBLIC SECTION.
    CONSTANTS c_version TYPE string VALUE '1.1.0'.
    CONSTANTS c_schema TYPE i VALUE 1.

    TYPES:
      BEGIN OF ty_dump,
        datum TYPE c LENGTH 8,
        uzeit TYPE c LENGTH 6,
        ahost TYPE c LENGTH 40,
        uname TYPE c LENGTH 12,
        mandt TYPE c LENGTH 3,
      END OF ty_dump,
      ty_dumps TYPE STANDARD TABLE OF ty_dump WITH EMPTY KEY,
      BEGIN OF ty_job,
        jobname  TYPE c LENGTH 32,
        jobcount TYPE c LENGTH 8,
        sdluname TYPE c LENGTH 12,
        status   TYPE c LENGTH 1,
        strtdate TYPE c LENGTH 8,
        strttime TYPE c LENGTH 6,
        enddate  TYPE c LENGTH 8,
        endtime  TYPE c LENGTH 6,
      END OF ty_job,
      ty_jobs TYPE STANDARD TABLE OF ty_job WITH EMPTY KEY,
      BEGIN OF ty_update,
        vbkey    TYPE c LENGTH 32,
        vbusr    TYPE c LENGTH 12,
        vbtcode  TYPE c LENGTH 20,
        vbreport TYPE c LENGTH 40,
        vbdate   TYPE c LENGTH 14,
        vbrc     TYPE c LENGTH 3,
      END OF ty_update,
      ty_updates TYPE STANDARD TABLE OF ty_update WITH EMPTY KEY,
      BEGIN OF ty_lock,
        gname   TYPE c LENGTH 30,
        garg    TYPE c LENGTH 150,
        gmode   TYPE c LENGTH 1,
        guname  TYPE c LENGTH 12,
        gclient TYPE c LENGTH 3,
        gtdate  TYPE c LENGTH 8,
        gttime  TYPE c LENGTH 6,
        gtcode  TYPE c LENGTH 20,
      END OF ty_lock,
      ty_locks TYPE STANDARD TABLE OF ty_lock WITH EMPTY KEY,
      BEGIN OF ty_trfc,
        arfcdest  TYPE c LENGTH 32,
        arfcstate TYPE c LENGTH 8,
        arfcfnam  TYPE c LENGTH 30,
        arfcuser  TYPE c LENGTH 12,
        arfcdatum TYPE c LENGTH 8,
        arfcuzeit TYPE c LENGTH 6,
      END OF ty_trfc,
      ty_trfcs TYPE STANDARD TABLE OF ty_trfc WITH EMPTY KEY,
      BEGIN OF ty_queue,
        qname   TYPE c LENGTH 24,
        dest    TYPE c LENGTH 32,
        qstate  TYPE c LENGTH 10,
        entries TYPE i,
      END OF ty_queue,
      ty_queues TYPE STANDARD TABLE OF ty_queue WITH EMPTY KEY,
      BEGIN OF ty_idoc,
        docnum TYPE c LENGTH 16,
        status TYPE c LENGTH 2,
        mestyp TYPE c LENGTH 30,
        direct TYPE c LENGTH 1,
        credat TYPE c LENGTH 8,
        cretim TYPE c LENGTH 6,
        sndprn TYPE c LENGTH 10,
        rcvprn TYPE c LENGTH 10,
      END OF ty_idoc,
      ty_idocs TYPE STANDARD TABLE OF ty_idoc WITH EMPTY KEY,
      BEGIN OF ty_number_range,
        client     TYPE c LENGTH 3,
        object     TYPE c LENGTH 10,
        subobject  TYPE c LENGTH 6,
        nrrangenr  TYPE c LENGTH 2,
        toyear     TYPE c LENGTH 4,
        fromnumber TYPE c LENGTH 20,
        tonumber   TYPE c LENGTH 20,
        nrlevel    TYPE c LENGTH 20,
        externind  TYPE c LENGTH 1,
      END OF ty_number_range,
      ty_number_ranges TYPE STANDARD TABLE OF ty_number_range WITH EMPTY KEY,
      BEGIN OF ty_applog,
        object    TYPE c LENGTH 20,
        subobject TYPE c LENGTH 20,
        logs      TYPE i,
      END OF ty_applog,
      ty_applogs TYPE STANDARD TABLE OF ty_applog WITH EMPTY KEY,
      BEGIN OF ty_batch_input,
        mandant TYPE c LENGTH 3,
        groupid TYPE c LENGTH 12,
        qstate  TYPE c LENGTH 1,
        credate TYPE c LENGTH 8,
        creator TYPE c LENGTH 12,
      END OF ty_batch_input,
      ty_batch_inputs TYPE STANDARD TABLE OF ty_batch_input WITH EMPTY KEY,
      BEGIN OF ty_client,
        mandt      TYPE c LENGTH 3,
        mtext      TYPE c LENGTH 25,
        cccategory TYPE c LENGTH 1,
        cccoractiv TYPE c LENGTH 1,
        ccnocliind TYPE c LENGTH 1,
        cccopylock TYPE c LENGTH 1,
      END OF ty_client,
      ty_clients TYPE STANDARD TABLE OF ty_client WITH EMPTY KEY,
      BEGIN OF ty_user,
        mandt TYPE c LENGTH 3,
        bname TYPE c LENGTH 12,
        uflag TYPE i,
        locnt TYPE i,
        trdat TYPE c LENGTH 8,
        ustyp TYPE c LENGTH 1,
      END OF ty_user,
      ty_users TYPE STANDARD TABLE OF ty_user WITH EMPTY KEY,
      BEGIN OF ty_locked_count,
        mandt  TYPE c LENGTH 3,
        locked TYPE i,
      END OF ty_locked_count,
      ty_locked_counts TYPE STANDARD TABLE OF ty_locked_count WITH EMPTY KEY,
      BEGIN OF ty_import,
        trkorr  TYPE c LENGTH 20,
        retcode TYPE c LENGTH 4,
        trtime  TYPE c LENGTH 14,
      END OF ty_import,
      ty_imports TYPE STANDARD TABLE OF ty_import WITH EMPTY KEY,
      ty_strings TYPE STANDARD TABLE OF string WITH EMPTY KEY,
      BEGIN OF ty_payload,
        schema        TYPE i,
        agent_version TYPE string,
        sid           TYPE c LENGTH 8,
        client        TYPE c LENGTH 3,
        host          TYPE c LENGTH 32,
        release       TYPE c LENGTH 4,
        dbsys         TYPE c LENGTH 10,
        timezone      TYPE c LENGTH 6,
        sent_at       TYPE c LENGTH 14,
        window_hours  TYPE i,
        dump_source   TYPE string,
        dump_log      TYPE string,
        dumps         TYPE ty_dumps,
        jobs          TYPE ty_jobs,
        updates       TYPE ty_updates,
        locks         TYPE ty_locks,
        trfc          TYPE ty_trfcs,
        qrfc_out      TYPE ty_queues,
        qrfc_in       TYPE ty_queues,
        idoc_errors   TYPE ty_idocs,
        idoc_total    TYPE i,
        number_ranges TYPE ty_number_ranges,
        applog_errors TYPE ty_applogs,
        batch_input   TYPE ty_batch_inputs,
        clients       TYPE ty_clients,
        critical_users TYPE ty_users,
        failed_logon_locks TYPE ty_locked_counts,
        spool_requests TYPE i,
        imports       TYPE ty_imports,
        errors        TYPE ty_strings,
      END OF ty_payload.

    "! Collect every area (each failure is recorded in ERRORS, never raised)
    METHODS collect
      RETURNING VALUE(rs_payload) TYPE ty_payload.

    "! Serialise and POST to the SM59 destination; returns the HTTP status (0 = not sent)
    METHODS push
      IMPORTING iv_destination TYPE rfcdest
                iv_token       TYPE string
                is_payload     TYPE ty_payload
      EXPORTING ev_status      TYPE i
                ev_response    TYPE string.

    METHODS constructor
      IMPORTING iv_max_rows TYPE i DEFAULT 2000.

  PRIVATE SECTION.
    DATA mv_max_rows TYPE i.
    DATA mv_since_date TYPE d.

    METHODS collect_dumps CHANGING cs_payload TYPE ty_payload.
    METHODS collect_jobs CHANGING cs_payload TYPE ty_payload.
    METHODS collect_updates CHANGING cs_payload TYPE ty_payload.
    METHODS collect_locks CHANGING cs_payload TYPE ty_payload.
    METHODS collect_rfc CHANGING cs_payload TYPE ty_payload.
    METHODS collect_idocs CHANGING cs_payload TYPE ty_payload.
    METHODS collect_number_ranges CHANGING cs_payload TYPE ty_payload.
    METHODS collect_applog CHANGING cs_payload TYPE ty_payload.
    METHODS collect_batch_input CHANGING cs_payload TYPE ty_payload.
    METHODS collect_security CHANGING cs_payload TYPE ty_payload.
    METHODS collect_spool_and_imports CHANGING cs_payload TYPE ty_payload.
    "! SELECT * over all clients where the table is client-specific, else a plain SELECT (cross-client tables)
    METHODS select_any_client
      IMPORTING iv_table TYPE string
                iv_where TYPE string OPTIONAL
      EXPORTING et_rows  TYPE STANDARD TABLE
      RAISING   cx_sy_dynamic_osql_error cx_sy_open_sql_db.
    METHODS add_error
      IMPORTING iv_area  TYPE string
                iv_text  TYPE string
      CHANGING  cs_payload TYPE ty_payload.
ENDCLASS.



CLASS zcl_sentinel_agent IMPLEMENTATION.

  METHOD constructor.
    mv_max_rows = iv_max_rows.
    " Rolling window: everything since yesterday at this time; Sentinel de-duplicates repeated rows.
    mv_since_date = sy-datum - 1.
  ENDMETHOD.


  METHOD collect.
    DATA lv_ts TYPE timestamp.
    DATA lv_tz TYPE c LENGTH 6.

    GET TIME STAMP FIELD lv_ts.
    rs_payload-schema = c_schema.
    rs_payload-agent_version = c_version.
    rs_payload-sid = sy-sysid.
    rs_payload-client = sy-mandt.
    rs_payload-host = sy-host.
    rs_payload-release = sy-saprl.
    rs_payload-dbsys = sy-dbsys.
    rs_payload-sent_at = |{ lv_ts }|.  " UTC, YYYYMMDDhhmmss
    rs_payload-window_hours = 24.
    SELECT SINGLE tzonesys FROM ttzcu INTO @lv_tz.
    rs_payload-timezone = lv_tz.

    collect_dumps( CHANGING cs_payload = rs_payload ).
    collect_jobs( CHANGING cs_payload = rs_payload ).
    collect_updates( CHANGING cs_payload = rs_payload ).
    collect_locks( CHANGING cs_payload = rs_payload ).
    collect_rfc( CHANGING cs_payload = rs_payload ).
    collect_idocs( CHANGING cs_payload = rs_payload ).
    collect_number_ranges( CHANGING cs_payload = rs_payload ).
    collect_applog( CHANGING cs_payload = rs_payload ).
    collect_batch_input( CHANGING cs_payload = rs_payload ).
    collect_security( CHANGING cs_payload = rs_payload ).
    collect_spool_and_imports( CHANGING cs_payload = rs_payload ).
  ENDMETHOD.


  METHOD collect_dumps.
    " Preferred: ST-PI /SDF/GET_DUMP_LOG (runtime error name per dump). Called dynamically so the class also
    " activates where ST-PI is missing; then the dump headers come from SNAP (time, server, user, client).
    DATA lr_log TYPE REF TO data.
    DATA lr_desc TYPE REF TO data.
    DATA lv_from TYPE d.
    DATA lv_cut TYPE i.
    FIELD-SYMBOLS <lt_log> TYPE STANDARD TABLE.
    FIELD-SYMBOLS <ls_desc> TYPE any.

    lv_from = mv_since_date.
    TRY.
        CREATE DATA lr_log TYPE STANDARD TABLE OF ('/SDF/E2E_LOG_STRUC').
        CREATE DATA lr_desc TYPE ('/SDF/E2E_LOG_STRUC_DESC').
        ASSIGN lr_log->* TO <lt_log>.
        ASSIGN lr_desc->* TO <ls_desc>.
        CALL FUNCTION '/SDF/GET_DUMP_LOG'
          EXPORTING
            date_from               = lv_from
            time_from               = sy-uzeit
            date_to                 = sy-datum
            time_to                 = sy-uzeit
          IMPORTING
            es_e2e_log_struct_desc  = <ls_desc>
          TABLES
            et_e2e_log              = <lt_log>
          EXCEPTIONS
            OTHERS                  = 1.
        IF sy-subrc = 0.
          IF lines( <lt_log> ) > mv_max_rows.
            lv_cut = mv_max_rows + 1.
            DELETE <lt_log> FROM lv_cut.
          ENDIF.
          cs_payload-dump_source = '/SDF/GET_DUMP_LOG'.
          " Rows and description are passed through unchanged; Sentinel maps the columns from the description.
          cs_payload-dump_log = |\{"ET_E2E_LOG":{ /ui2/cl_json=>serialize( data = <lt_log> ) },| &&
                                |"ES_E2E_LOG_STRUCT_DESC":{ /ui2/cl_json=>serialize( data = <ls_desc> ) }\}|.
          RETURN.
        ENDIF.
      CATCH cx_sy_create_data_error cx_sy_dyn_call_error.
        " ST-PI not installed: fall back to SNAP below
    ENDTRY.

    TRY.
        SELECT datum, uzeit, ahost, uname, mandt
          FROM snap
          WHERE seqno = '000'
            AND datum >= @lv_from
          ORDER BY datum DESCENDING, uzeit DESCENDING
          INTO CORRESPONDING FIELDS OF TABLE @cs_payload-dumps
          UP TO @mv_max_rows ROWS.
        cs_payload-dump_source = 'SNAP'.
      CATCH cx_sy_open_sql_db INTO DATA(lx_sql).
        add_error( EXPORTING iv_area = `dumps` iv_text = lx_sql->get_text( ) CHANGING cs_payload = cs_payload ).
    ENDTRY.
  ENDMETHOD.


  METHOD collect_jobs.
    TRY.
        SELECT jobname, jobcount, sdluname, status, strtdate, strttime, enddate, endtime
          FROM tbtco
          WHERE strtdate >= @mv_since_date
             OR status = 'R'
          INTO CORRESPONDING FIELDS OF TABLE @cs_payload-jobs
          UP TO @mv_max_rows ROWS.
      CATCH cx_sy_open_sql_db INTO DATA(lx_sql).
        add_error( EXPORTING iv_area = `jobs` iv_text = lx_sql->get_text( ) CHANGING cs_payload = cs_payload ).
    ENDTRY.
  ENDMETHOD.


  METHOD collect_updates.
    TRY.
        SELECT vbkey, vbusr, vbtcode, vbreport, vbdate, vbrc
          FROM vbhdr
          WHERE vbrc <> 0
          INTO CORRESPONDING FIELDS OF TABLE @cs_payload-updates
          UP TO @mv_max_rows ROWS.
      CATCH cx_sy_open_sql_db INTO DATA(lx_sql).
        add_error( EXPORTING iv_area = `updates` iv_text = lx_sql->get_text( ) CHANGING cs_payload = cs_payload ).
    ENDTRY.
  ENDMETHOD.


  METHOD collect_locks.
    DATA lt_enq TYPE STANDARD TABLE OF seqg3 WITH EMPTY KEY.
    DATA ls_lock TYPE ty_lock.

    CALL FUNCTION 'ENQUEUE_READ'
      EXPORTING
        gclient               = '*'
        guname                = '*'
      TABLES
        enq                   = lt_enq
      EXCEPTIONS
        communication_failure = 1
        system_failure        = 2
        OTHERS                = 3.
    IF sy-subrc <> 0.
      add_error( EXPORTING iv_area = `locks` iv_text = |ENQUEUE_READ sy-subrc { sy-subrc }| CHANGING cs_payload = cs_payload ).
      RETURN.
    ENDIF.
    LOOP AT lt_enq ASSIGNING FIELD-SYMBOL(<ls_enq>).
      IF sy-tabix > mv_max_rows.
        EXIT.
      ENDIF.
      CLEAR ls_lock.
      MOVE-CORRESPONDING <ls_enq> TO ls_lock.
      APPEND ls_lock TO cs_payload-locks.
    ENDLOOP.
  ENDMETHOD.


  METHOD collect_rfc.
    TRY.
        SELECT arfcdest, arfcstate, arfcfnam, arfcuser, arfcdatum, arfcuzeit
          FROM arfcsstate CLIENT SPECIFIED
          WHERE arfcstate IN ('SYSFAIL', 'CPICERR', 'ANORETRY', 'SYSLOAD', 'ARETRY')
          INTO CORRESPONDING FIELDS OF TABLE @cs_payload-trfc
          UP TO @mv_max_rows ROWS.
      CATCH cx_sy_open_sql_db INTO DATA(lx_trfc).
        add_error( EXPORTING iv_area = `trfc` iv_text = lx_trfc->get_text( ) CHANGING cs_payload = cs_payload ).
    ENDTRY.
    TRY.
        SELECT qname, dest, qstate, COUNT(*) AS entries
          FROM trfcqout CLIENT SPECIFIED
          GROUP BY qname, dest, qstate
          INTO CORRESPONDING FIELDS OF TABLE @cs_payload-qrfc_out
          UP TO @mv_max_rows ROWS.
        SELECT qname, qstate, COUNT(*) AS entries
          FROM trfcqin CLIENT SPECIFIED
          GROUP BY qname, qstate
          INTO CORRESPONDING FIELDS OF TABLE @cs_payload-qrfc_in
          UP TO @mv_max_rows ROWS.
      CATCH cx_sy_open_sql_db INTO DATA(lx_qrfc).
        add_error( EXPORTING iv_area = `qrfc` iv_text = lx_qrfc->get_text( ) CHANGING cs_payload = cs_payload ).
    ENDTRY.
  ENDMETHOD.


  METHOD collect_idocs.
    TRY.
        SELECT docnum, status, mestyp, direct, credat, cretim, sndprn, rcvprn
          FROM edidc CLIENT SPECIFIED
          WHERE credat >= @mv_since_date
            AND status IN ('02', '04', '05', '25', '26', '29', '51', '56', '60', '61', '63', '65')
          INTO CORRESPONDING FIELDS OF TABLE @cs_payload-idoc_errors
          UP TO @mv_max_rows ROWS.
        SELECT COUNT(*) FROM edidc CLIENT SPECIFIED WHERE credat >= @mv_since_date INTO @cs_payload-idoc_total.
      CATCH cx_sy_open_sql_db INTO DATA(lx_sql).
        add_error( EXPORTING iv_area = `idocs` iv_text = lx_sql->get_text( ) CHANGING cs_payload = cs_payload ).
    ENDTRY.
  ENDMETHOD.


  METHOD select_any_client.
    CLEAR et_rows.
    TRY.
        IF iv_where IS INITIAL.
          SELECT * FROM (iv_table) CLIENT SPECIFIED INTO CORRESPONDING FIELDS OF TABLE @et_rows UP TO @mv_max_rows ROWS.
        ELSE.
          SELECT * FROM (iv_table) CLIENT SPECIFIED WHERE (iv_where)
            INTO CORRESPONDING FIELDS OF TABLE @et_rows UP TO @mv_max_rows ROWS.
        ENDIF.
      CATCH cx_sy_dynamic_osql_semantics.
        " cross-client table: CLIENT SPECIFIED is not allowed, a plain SELECT already returns every row
        IF iv_where IS INITIAL.
          SELECT * FROM (iv_table) INTO CORRESPONDING FIELDS OF TABLE @et_rows UP TO @mv_max_rows ROWS.
        ELSE.
          SELECT * FROM (iv_table) WHERE (iv_where) INTO CORRESPONDING FIELDS OF TABLE @et_rows UP TO @mv_max_rows ROWS.
        ENDIF.
    ENDTRY.
  ENDMETHOD.


  METHOD collect_number_ranges.
    " SNRO: internal number ranges that are at least half used (external ranges are numbered by the application).
    DATA lt_nriv TYPE ty_number_ranges.
    DATA lv_from TYPE decfloat34.
    DATA lv_to TYPE decfloat34.
    DATA lv_level TYPE decfloat34.
    TRY.
        select_any_client( EXPORTING iv_table = `NRIV` iv_where = `EXTERNIND = ' '` IMPORTING et_rows = lt_nriv ).
      CATCH cx_sy_dynamic_osql_error cx_sy_open_sql_db INTO DATA(lx_sql).
        add_error( EXPORTING iv_area = `number_ranges` iv_text = lx_sql->get_text( ) CHANGING cs_payload = cs_payload ).
        RETURN.
    ENDTRY.
    LOOP AT lt_nriv INTO DATA(ls_nriv).
      IF ls_nriv-fromnumber CN ' 0123456789' OR ls_nriv-tonumber CN ' 0123456789' OR ls_nriv-nrlevel CN ' 0123456789'.
        CONTINUE.  " alphanumeric range
      ENDIF.
      lv_from = condense( ls_nriv-fromnumber ).
      lv_to = condense( ls_nriv-tonumber ).
      lv_level = condense( ls_nriv-nrlevel ).
      IF lv_to > lv_from AND ( lv_level - lv_from ) * 2 >= lv_to - lv_from.
        APPEND ls_nriv TO cs_payload-number_ranges.
      ENDIF.
    ENDLOOP.
  ENDMETHOD.


  METHOD collect_applog.
    " SLG1: application logs of the last 24 hours that contain abort (A) or error (E) messages, by object.
    " The condition is dynamic so that a release without these BALHDR fields reports an error instead of failing activation.
    DATA lv_where TYPE string.
    lv_where = |ALDATE >= '{ mv_since_date }' AND ( MSG_CNT_A > 0 OR MSG_CNT_E > 0 )|.
    TRY.
        SELECT object, subobject, COUNT(*) AS logs
          FROM balhdr CLIENT SPECIFIED
          WHERE (lv_where)
          GROUP BY object, subobject
          INTO CORRESPONDING FIELDS OF TABLE @cs_payload-applog_errors
          UP TO @mv_max_rows ROWS.
      CATCH cx_sy_dynamic_osql_error cx_sy_open_sql_db INTO DATA(lx_sql).
        add_error( EXPORTING iv_area = `applog` iv_text = lx_sql->get_text( ) CHANGING cs_payload = cs_payload ).
    ENDTRY.
  ENDMETHOD.


  METHOD collect_batch_input.
    " SM35: sessions in error created in the last 7 days.
    DATA lv_week TYPE d.
    lv_week = sy-datum - 7.
    TRY.
        SELECT mandant, groupid, qstate, credate, creator
          FROM apqi CLIENT SPECIFIED
          WHERE qstate = 'E'
            AND credate >= @lv_week
          INTO CORRESPONDING FIELDS OF TABLE @cs_payload-batch_input
          UP TO @mv_max_rows ROWS.
      CATCH cx_sy_open_sql_db INTO DATA(lx_sql).
        add_error( EXPORTING iv_area = `batch_input` iv_text = lx_sql->get_text( ) CHANGING cs_payload = cs_payload ).
    ENDTRY.
  ENDMETHOD.


  METHOD collect_security.
    " SCC4 client settings and the state of the standard users (SU01), across clients.
    TRY.
        select_any_client( EXPORTING iv_table = `T000` IMPORTING et_rows = cs_payload-clients ).
      CATCH cx_sy_dynamic_osql_error cx_sy_open_sql_db INTO DATA(lx_t000).
        add_error( EXPORTING iv_area = `clients` iv_text = lx_t000->get_text( ) CHANGING cs_payload = cs_payload ).
    ENDTRY.
    TRY.
        SELECT mandt, bname, uflag, locnt, trdat, ustyp
          FROM usr02 CLIENT SPECIFIED
          WHERE bname IN ('SAP*', 'DDIC', 'TMSADM', 'EARLYWATCH', 'SAPCPIC')
          INTO CORRESPONDING FIELDS OF TABLE @cs_payload-critical_users.
        SELECT mandt, COUNT(*) AS locked
          FROM usr02 CLIENT SPECIFIED
          WHERE uflag >= 128
          GROUP BY mandt
          INTO CORRESPONDING FIELDS OF TABLE @cs_payload-failed_logon_locks.
      CATCH cx_sy_open_sql_db INTO DATA(lx_usr).
        add_error( EXPORTING iv_area = `users` iv_text = lx_usr->get_text( ) CHANGING cs_payload = cs_payload ).
    ENDTRY.
  ENDMETHOD.


  METHOD collect_spool_and_imports.
    DATA lv_since TYPE c LENGTH 14.
    TRY.
        SELECT COUNT(*) FROM tsp01 INTO @cs_payload-spool_requests.
      CATCH cx_sy_open_sql_db INTO DATA(lx_spool).
        add_error( EXPORTING iv_area = `spool` iv_text = lx_spool->get_text( ) CHANGING cs_payload = cs_payload ).
    ENDTRY.
    lv_since = |{ mv_since_date }000000|.
    TRY.
        SELECT trkorr, retcode, trtime
          FROM tpalog
          WHERE trtime >= @lv_since
          INTO CORRESPONDING FIELDS OF TABLE @cs_payload-imports
          UP TO @mv_max_rows ROWS.
      CATCH cx_sy_open_sql_db INTO DATA(lx_tp).
        add_error( EXPORTING iv_area = `imports` iv_text = lx_tp->get_text( ) CHANGING cs_payload = cs_payload ).
    ENDTRY.
  ENDMETHOD.


  METHOD add_error.
    APPEND |{ iv_area }: { iv_text }| TO cs_payload-errors.
  ENDMETHOD.


  METHOD push.
    DATA lo_http TYPE REF TO if_http_client.
    DATA lv_json TYPE string.
    DATA lv_reason TYPE string.

    CLEAR: ev_status, ev_response.
    lv_json = /ui2/cl_json=>serialize( data = is_payload pretty_name = /ui2/cl_json=>pretty_mode-low_case ).

    cl_http_client=>create_by_destination(
      EXPORTING
        destination              = iv_destination
      IMPORTING
        client                   = lo_http
      EXCEPTIONS
        argument_not_found       = 1
        destination_not_found    = 2
        destination_no_authority = 3
        plugin_not_active        = 4
        internal_error           = 5
        OTHERS                   = 6 ).
    IF sy-subrc <> 0.
      ev_response = |SM59 destination { iv_destination } not usable (sy-subrc { sy-subrc })|.
      RETURN.
    ENDIF.

    lo_http->request->set_method( if_http_request=>co_request_method_post ).
    lo_http->request->set_header_field( name = 'Content-Type' value = 'application/json; charset=utf-8' ).
    lo_http->request->set_header_field( name = 'X-Sentinel-Agent-Token' value = iv_token ).
    lo_http->request->set_header_field( name = 'X-Sentinel-Agent-Version' value = c_version ).
    lo_http->request->set_cdata( lv_json ).

    lo_http->send( EXCEPTIONS http_communication_failure = 1 http_invalid_state = 2 http_processing_failed = 3 OTHERS = 4 ).
    IF sy-subrc = 0.
      lo_http->receive( EXCEPTIONS http_communication_failure = 1 http_invalid_state = 2 http_processing_failed = 3 OTHERS = 4 ).
    ENDIF.
    IF sy-subrc <> 0.
      lo_http->get_last_error( IMPORTING message = ev_response ).
      lo_http->close( ).
      RETURN.
    ENDIF.
    lo_http->response->get_status( IMPORTING code = ev_status reason = lv_reason ).
    ev_response = lo_http->response->get_cdata( ).
    IF ev_response IS INITIAL.
      ev_response = lv_reason.
    ENDIF.
    lo_http->close( ).
  ENDMETHOD.

ENDCLASS.
