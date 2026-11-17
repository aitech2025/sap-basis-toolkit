import { MonitorCard, NoData, Trend, UploadFallback, useMonitor } from "../../components/monitor";
import { DataTable, ErrorBanner, Pill, Tile, fmtDate } from "../../components/ui";
import type { SystemDetail } from "../../types";

type Row = Record<string, string | number | boolean | null>;

export default function ErrorsTab({ system }: { system: SystemDetail }) {
  const dumps = useMonitor(system, "dumps");
  const syslog = useMonitor(system, "syslog");
  const updates = useMonitor(system, "updates");
  const d = dumps.data?.latest?.data as { summary: { last_hour: number; last_24h: number; top_errors: { error: string; count: number }[]; top_users: { user: string; count: number }[] }; recent: Row[] } | undefined;
  const s = syslog.data?.latest?.data as { summary: { entries_24h: number; errors_last_hour: number; errors_24h: number; top_messages: { msgno: string; count: number }[] }; recent: Row[] } | undefined;
  const u = updates.data?.latest?.data as { summary: { errors: number; users: number }; recent: Row[] } | undefined;

  return (
    <>
      <ErrorBanner error={dumps.error || syslog.error || updates.error} />
      <div className="tiles">
        <Tile label="Short dumps (1 h)" value={d?.summary.last_hour ?? "–"} hint={d ? `${d.summary.last_24h} in 24 h` : "no data"} />
        <Tile label="Syslog errors (1 h)" value={s?.summary.errors_last_hour ?? "–"} hint={s ? `${s.summary.errors_24h} in 24 h` : "no data"} />
        <Tile label="Update errors" value={u?.summary.errors ?? "–"} hint={u ? `${u.summary.users} users affected` : "no data"} />
      </div>

      <MonitorCard title="ABAP short dumps (ST22)" detail={dumps.data}>
        {!d ? <NoData system={system} what="short dump" hint="HANA-based systems read table SNAP through the HANA collector." /> : (
          <>
            <div className="cols">
              <div>
                <h3>Dumps per hour (collector samples)</h3>
                <Trend points={dumps.data?.series["dumps.last_hour"]} unit="dumps/h" label="Short dumps per hour" threshold={{ value: 20, label: "warning" }} />
              </div>
              <div>
                <h3>Top runtime errors (24 h)</h3>
                <DataTable rows={d.summary.top_errors} rowKey={(r) => r.error} columns={[
                  { key: "error", label: "Runtime error", render: (r) => <code>{r.error}</code> }, { key: "count", label: "Count", numeric: true }]} />
              </div>
            </div>
            <h3 style={{ marginTop: 12 }}>Recent dumps</h3>
            <DataTable<Row> rows={d.recent.slice(0, 25)} rowKey={(r, i) => `${r.at}-${i}`} columns={[
              { key: "at", label: "When", render: (r) => fmtDate(String(r.at)) }, { key: "error", label: "Runtime error", render: (r) => <code>{String(r.error)}</code> },
              { key: "user", label: "User" }, { key: "client", label: "Client" }, { key: "host", label: "Server" }]} />
          </>
        )}
        <UploadFallback system={system} monitor="dumps" label="ST22" onDone={dumps.reload}
          how="ST22 → Today/Yesterday → list → Export → Spreadsheet (Date, Time, Application Server, User, Client, Runtime Error)." />
      </MonitorCard>

      <MonitorCard title="System log (SM21)" detail={syslog.data}>
        {!s ? <NoData system={system} what="system log" hint="Read via sapcontrol ABAPReadSyslog where the web method is available." /> : (
          <>
            <DataTable<Row> rows={s.recent.slice(0, 40)} rowKey={(r, i) => `${r.at}-${i}`} columns={[
              { key: "at", label: "When", render: (r) => fmtDate(String(r.at)) },
              { key: "is_error", label: "", render: (r) => r.is_error ? <Pill value="error" /> : <span className="muted small">{String(r.severity || "info")}</span> },
              { key: "msgno", label: "Msg", render: (r) => <code>{String(r.msgno ?? "")}</code> }, { key: "text", label: "Text" },
              { key: "user", label: "User" }, { key: "instance", label: "Instance" }]} />
          </>
        )}
        <UploadFallback system={system} monitor="syslog" label="SM21" onDone={syslog.reload}
          how="SM21 → select period → System Log → Download → Spreadsheet (Date, Time, Instance, User, MNo, Message Text)." />
      </MonitorCard>

      <MonitorCard title="Update errors (SM13)" detail={updates.data}>
        {!u ? <NoData system={system} what="update" hint="HANA-based systems read VBHDR through the HANA collector (verify the query on your release)." /> : (
          <DataTable<Row> rows={u.recent} rowKey={(r, i) => `${r.at}-${i}`} empty="No failed update requests." columns={[
            { key: "at", label: "When", render: (r) => fmtDate(r.at ? String(r.at) : null) }, { key: "user", label: "User" },
            { key: "tcode", label: "Tcode" }, { key: "report", label: "Report" }, { key: "state", label: "State" }]} />
        )}
        <UploadFallback system={system} monitor="updates" label="SM13" onDone={updates.reload}
          how="SM13 → status 'All' → list → Export → Spreadsheet (Client, User, Date, Time, Tcode, Status)." />
      </MonitorCard>
    </>
  );
}
