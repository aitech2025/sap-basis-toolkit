import { Freshness, NoData, Trend, useMonitor } from "../../components/monitor";
import { Card, DataTable, Empty, Pill, Tile, fmtNumber } from "../../components/ui";
import type { SystemDetail } from "../../types";

type Range = { client: string | null; object: string; subobject: string | null; range: string; year: string | null; used_pct: number; remaining: number };
type Finding = { severity: string; check: string; detail: string };
type Client = { client: string; text: string; role: string; client_specific_changes: string; cross_client_changes: string; open: boolean };
type StdUser = { client: string; user: string; locked: boolean; failed_logons: number; type: string; last_logon: string | null };
type AppLog = { object: string; subobject: string; logs: number };
type Session = { client: string; session: string; created: string | null; creator: string };
type Buffer = { instance: string; buffer: string; hit_ratio: number };

/** SNRO, SCC4, SU01, SLG1, SM35, SP01 and ST02: the configuration and capacity side of the daily checks. */
export default function BasisChecksTab({ system }: { system: SystemDetail }) {
  const nr = useMonitor(system, "number_ranges", 24 * 7);
  const sec = useMonitor(system, "security");
  const log = useMonitor(system, "applog");
  const bi = useMonitor(system, "batch_input");
  const spool = useMonitor(system, "spool", 24 * 7);
  const live = useMonitor(system, "abap_live");
  const n = nr.data?.latest?.data as { summary: { max_used_pct: number; over_80: number; ranges_reported: number }; ranges: Range[] } | undefined;
  const s = sec.data?.latest?.data as { summary: { findings: number; open_clients: number; environment: string }; findings: Finding[]; clients: Client[]; standard_users: StdUser[] } | undefined;
  const a = log.data?.latest?.data as { summary: { error_logs_24h: number; objects: number }; by_object: AppLog[] } | undefined;
  const b = bi.data?.latest?.data as { summary: { error_sessions_7d: number }; sessions: Session[] } | undefined;
  const sp = spool.data?.latest?.data as { summary: { requests: number | null } } | undefined;
  const buffers = ((live.data?.latest?.data as { buffers?: Buffer[] } | undefined)?.buffers) ?? [];
  const nothing = [nr, sec, log, bi, spool].every((m) => m.data && !m.data.latest);

  if (nothing) {
    return <Card title="Basis checks"><NoData system={system} what="Basis check"
      hint="Number ranges, client settings, standard users, application logs and batch input come from the ABAP push agent (or, for some of them, the HANA SQL collector with an ABAP schema)." /></Card>;
  }
  return (
    <>
      <div className="tiles">
        <Tile label="Number range used (max)" value={n ? `${fmtNumber(n.summary.max_used_pct, 1)}%` : "–"}
          tone={n ? (n.summary.max_used_pct >= 90 ? "bad" : n.summary.max_used_pct >= 80 ? "warn" : undefined) : undefined}
          hint={n ? `${n.summary.over_80} ranges over 80%` : "SNRO"} />
        <Tile label="Security findings" value={s ? s.summary.findings : "–"} tone={s?.summary.findings ? "warn" : undefined} hint="SCC4, SU01" />
        <Tile label="Application log errors (24 h)" value={a ? fmtNumber(a.summary.error_logs_24h, 0) : "–"} hint={a ? `${a.summary.objects} objects` : "SLG1"} />
        <Tile label="Batch input errors (7 days)" value={b ? b.summary.error_sessions_7d : "–"} tone={b?.summary.error_sessions_7d ? "warn" : undefined} hint="SM35" />
        <Tile label="Spool requests" value={sp?.summary.requests != null ? fmtNumber(sp.summary.requests, 0) : "–"} hint="SP01 / TSP01" />
      </div>

      <Card title={<>Number ranges (SNRO) <Freshness detail={nr.data} /></>}>
        <DataTable<Range> rows={n?.ranges ?? []} rowKey={(r) => `${r.client}-${r.object}-${r.subobject}-${r.range}-${r.year}`}
          empty="No internal number range is more than half used."
          columns={[
            { key: "used_pct", label: "Used", numeric: true, render: (r) => <span className={r.used_pct >= 90 ? "font-semibold text-bad" : r.used_pct >= 80 ? "font-semibold text-warn" : ""}>{fmtNumber(r.used_pct, 1)}%</span> },
            { key: "object", label: "Object", render: (r) => <code>{r.object}</code> }, { key: "subobject", label: "Sub-object" },
            { key: "range", label: "Interval" }, { key: "year", label: "Year" }, { key: "client", label: "Client" },
            { key: "remaining", label: "Numbers left", numeric: true, render: (r) => fmtNumber(r.remaining, 0) },
          ]} />
        <p className="muted small">A full number range stops document creation. Extend the interval or add a new one in SNRO well before 100%.</p>
      </Card>

      <div className="cols">
        <Card title={<>Security baseline <Freshness detail={sec.data} /></>}>
          {s ? (
            <>
              {s.findings.length ? (
                <ul className="space-y-2">
                  {s.findings.map((f, i) => (
                    <li key={i} className="flex gap-2 text-sm"><Pill value={f.severity === "info" ? "info" : "warning"} /><span><strong>{f.check}</strong> {f.detail}</span></li>
                  ))}
                </ul>
              ) : <Empty>No findings: production clients are closed and SAP* is locked in every client.</Empty>}
              <h3 className="mt-4">Clients (SCC4)</h3>
              <DataTable<Client> rows={s.clients} rowKey={(c) => c.client}
                columns={[{ key: "client", label: "Client" }, { key: "text", label: "Name" }, { key: "role", label: "Role" },
                          { key: "client_specific_changes", label: "Client-specific changes" }, { key: "cross_client_changes", label: "Cross-client changes" },
                          { key: "open", label: "Status", render: (c) => c.open ? <strong className="text-warn">Open</strong> : <span className="text-muted-foreground">Closed</span> }]} />
              {!!s.standard_users.length && (
                <>
                  <h3 className="mt-4">Standard users (SU01)</h3>
                  <DataTable<StdUser> rows={s.standard_users} rowKey={(u) => `${u.client}-${u.user}`}
                    columns={[{ key: "client", label: "Client" }, { key: "user", label: "User", render: (u) => <code>{u.user}</code> },
                              { key: "locked", label: "Locked", render: (u) => u.locked ? "yes" : "no" }, { key: "type", label: "Type" },
                              { key: "failed_logons", label: "Failed logons", numeric: true }, { key: "last_logon", label: "Last logon" }]} />
                </>
              )}
            </>
          ) : <Empty>Client settings and standard users come from the ABAP push agent.</Empty>}
        </Card>
        <Card title={<>ABAP buffers (ST02) <Freshness detail={live.data} /></>}>
          <DataTable<Buffer> rows={buffers} rowKey={(x) => `${x.instance}-${x.buffer}`} empty="Buffer values come from CCMS through the sapcontrol collector."
            columns={[{ key: "instance", label: "Instance" }, { key: "buffer", label: "Buffer" },
                      { key: "hit_ratio", label: "Hit ratio", numeric: true, render: (x) => <span className={x.hit_ratio < 95 ? "text-warn font-semibold" : ""}>{fmtNumber(x.hit_ratio, 1)}%</span> }]} />
          <h3 className="mt-4">Buffer swaps</h3>
          <Trend points={live.data?.series["abap.buffer_swaps"]} unit="swaps" label="Buffer swaps, highest instance" threshold={{ value: 1000, label: "1000 warning" }} />
        </Card>
      </div>

      <div className="cols">
        <Card title={<>Application log errors (SLG1, 24 h) <Freshness detail={log.data} /></>}>
          <DataTable<AppLog> rows={a?.by_object ?? []} rowKey={(r) => `${r.object}-${r.subobject}`} empty="No application logs with errors."
            columns={[{ key: "object", label: "Object", render: (r) => <code>{r.object}</code> }, { key: "subobject", label: "Sub-object" },
                      { key: "logs", label: "Logs with errors", numeric: true }]} />
        </Card>
        <Card title={<>Batch input sessions in error (SM35) <Freshness detail={bi.data} /></>}>
          <DataTable<Session> rows={b?.sessions ?? []} rowKey={(r) => `${r.client}-${r.session}-${r.created}`} empty="No sessions in error in the last 7 days."
            columns={[{ key: "session", label: "Session", render: (r) => <code>{r.session}</code> }, { key: "client", label: "Client" },
                      { key: "creator", label: "Created by" }, { key: "created", label: "Created", render: (r) => r.created?.slice(0, 10) ?? "–" }]} />
        </Card>
      </div>
      <Card title="Number range fill, 7 days">
        <Trend points={nr.data?.series["numrange.max_used_pct"]} unit="%" label="Highest number range fill level" threshold={{ value: 80, label: "80% warning" }} />
      </Card>
    </>
  );
}
