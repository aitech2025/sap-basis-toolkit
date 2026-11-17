import { MonitorCard, NoData, Trend, UploadFallback, useMonitor } from "../../components/monitor";
import { DataTable, ErrorBanner, Pill, Tile, fmtDate } from "../../components/ui";
import type { SystemDetail } from "../../types";

type Row = Record<string, string | number | null>;

export default function InterfacesTab({ system }: { system: SystemDetail }) {
  const trfc = useMonitor(system, "trfc");
  const qrfc = useMonitor(system, "qrfc");
  const idocs = useMonitor(system, "idocs");
  const t = trfc.data?.latest?.data as { summary: { errors: number; destinations: number; by_destination: Row[] }; recent: Row[] } | undefined;
  const q = qrfc.data?.latest?.data as { summary: { queues: number; blocked_queues: number; entries: number; blocked_entries: number }; blocked: Row[]; largest: Row[] } | undefined;
  const i = idocs.data?.latest?.data as { summary: { errors_24h: number; total_24h: number | null; by_status: Row[]; by_message_type: Row[] }; recent: Row[] } | undefined;

  return (
    <>
      <ErrorBanner error={trfc.error || qrfc.error || idocs.error} />
      <div className="tiles">
        <Tile label="tRFC errors" value={t?.summary.errors ?? "–"} hint={t ? `${t.summary.destinations} destinations` : "no data"} />
        <Tile label="Blocked qRFC queues" value={q?.summary.blocked_queues ?? "–"} hint={q ? `${q.summary.blocked_entries} entries waiting` : "no data"} />
        <Tile label="IDoc errors (24 h)" value={i?.summary.errors_24h ?? "–"} hint={i?.summary.total_24h != null ? `of ${i.summary.total_24h} IDocs` : "no data"} />
      </div>

      <MonitorCard title="tRFC (SM58)" detail={trfc.data}>
        {!t ? <NoData system={system} what="tRFC" hint="HANA-based systems read ARFCSSTATE through the HANA collector." /> : (
          <div className="cols">
            <DataTable rows={t.summary.by_destination} rowKey={(r, n) => `${r.dest}-${n}`} empty="No tRFC errors." columns={[
              { key: "dest", label: "Destination", render: (r) => <code>{String(r.dest)}</code> }, { key: "state", label: "State", render: (r) => <><Pill value="error" /> {String(r.state)}</> },
              { key: "count", label: "LUWs", numeric: true }]} />
            <DataTable rows={t.recent.slice(0, 15)} rowKey={(r, n) => `${r.at}-${n}`} empty="–" columns={[
              { key: "at", label: "When", render: (r) => fmtDate(r.at ? String(r.at) : null) }, { key: "function", label: "Function" },
              { key: "dest", label: "Destination" }, { key: "state", label: "State" }]} />
          </div>
        )}
        <UploadFallback system={system} monitor="trfc" label="SM58" onDone={trfc.reload}
          how="SM58 → all users/destinations → Export → Spreadsheet (Caller, Function Module, Target System, Date, Time, Status Text)." />
      </MonitorCard>

      <MonitorCard title="qRFC queues (SMQ1 / SMQ2)" detail={qrfc.data}>
        {!q ? <NoData system={system} what="qRFC" hint="HANA-based systems read TRFCQOUT/TRFCQIN through the HANA collector." /> : (
          <>
            <Trend points={qrfc.data?.series["qrfc.blocked_queues"]} unit="queues" label="Blocked qRFC queues" />
            <DataTable rows={q.blocked} rowKey={(r, n) => `${r.qname}-${n}`} empty="No blocked queues." columns={[
              { key: "direction", label: "Dir.", render: (r) => r.direction === "in" ? "SMQ2 in" : "SMQ1 out" },
              { key: "qname", label: "Queue", render: (r) => <code>{String(r.qname)}</code> }, { key: "dest", label: "Destination" },
              { key: "state", label: "State", render: (r) => <><Pill value="error" /> {String(r.state)}</> }, { key: "entries", label: "Entries", numeric: true }]} />
          </>
        )}
        <UploadFallback system={system} monitor="qrfc" label="SMQ1/SMQ2" onDone={qrfc.reload}
          how="SMQ1 (outbound) or SMQ2 (inbound) → Export → Spreadsheet (Client, Queue Name, Destination, Entries, Status)." />
      </MonitorCard>

      <MonitorCard title="IDocs (WE02 / BD87)" detail={idocs.data}>
        {!i ? <NoData system={system} what="IDoc" hint="HANA-based systems read EDIDC through the HANA collector." /> : (
          <>
            <div className="cols">
              <DataTable rows={i.summary.by_status} rowKey={(r) => String(r.status)} empty="No IDoc errors in 24 h." columns={[
                { key: "status", label: "Status", render: (r) => <code>{String(r.status)}</code> }, { key: "text", label: "Meaning" },
                { key: "count", label: "IDocs", numeric: true }]} />
              <DataTable rows={i.summary.by_message_type} rowKey={(r) => String(r.mestyp)} empty="–" columns={[
                { key: "mestyp", label: "Message type" }, { key: "count", label: "Errors", numeric: true }]} />
            </div>
            <h3 style={{ marginTop: 12 }}>Recent failed IDocs</h3>
            <DataTable rows={i.recent.slice(0, 25)} rowKey={(r) => String(r.docnum)} columns={[
              { key: "at", label: "Created", render: (r) => fmtDate(String(r.at)) }, { key: "docnum", label: "IDoc" },
              { key: "status", label: "Status", render: (r) => <span title={String(r.status_text)}>{String(r.status)}</span> },
              { key: "mestyp", label: "Message type" }, { key: "direction", label: "Dir.", render: (r) => r.direction === "1" ? "out" : "in" },
              { key: "partner", label: "Partner" }]} />
          </>
        )}
        <UploadFallback system={system} monitor="idocs" label="WE02" onDone={idocs.reload}
          how="WE02 → created in last 24 h → list → Export → Spreadsheet (IDoc number, Status, Message Type, Direction, Created on, Time)." />
      </MonitorCard>
    </>
  );
}
