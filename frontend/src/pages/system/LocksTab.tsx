import { useState } from "react";
import { api } from "../../api";
import { LineChart } from "../../components/LineChart";
import { Card, DataTable, Empty, ErrorBanner, FileField, Pill, Tile, fmtDate, useAsync } from "../../components/ui";
import type { LockView, SystemDetail } from "../../types";

const age = (m: number | null | undefined) => (m == null ? "–" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`);

export default function LocksTab({ system }: { system: SystemDetail }) {
  const cap = system.capabilities.locks;
  const [days, setDays] = useState(7);
  const { data, error, reload } = useAsync(() => (cap.supported ? api.locksReport(system.id, days) : Promise.resolve(null)), [system.id, days]);
  const [csv, setCsv] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  if (!cap.supported) return <Card title="SAP locks"><Empty>{cap.how_to_export}</Empty></Card>;
  const s = data?.summary;
  const t = data?.thresholds;
  const lockColumns = [
    { key: "severity", label: "Age", render: (l: LockView) => <><Pill value={l.severity === "ok" ? "healthy" : l.severity} /> {age(l.age_minutes)}</> },
    { key: "user", label: "User", render: (l: LockView) => <strong>{l.user}</strong> },
    { key: "table", label: "Table" },
    { key: "argument", label: "Lock argument", render: (l: LockView) => <code>{l.argument ?? "–"}</code> },
    { key: "mode", label: "Mode", render: (l: LockView) => l.mode ?? "–" },
    { key: "client", label: "Client", render: (l: LockView) => l.client ?? "–" },
    { key: "locked_at", label: "Locked since", render: (l: LockView) => fmtDate(l.locked_at) },
    { key: "seen_in_snapshots", label: "Snapshots", numeric: true },
  ];

  return (
    <>
      <ErrorBanner error={error} />
      {message && <div className={`banner ${message.ok ? "ok" : "error"}`}>{message.text}</div>}
      {data?.stale && data.latest && <div className="banner info">Latest SM12 snapshot is from {fmtDate(data.latest)} — upload a fresh one for current locks.</div>}
      <div className="tiles">
        <Tile label="Current locks" value={s?.locks ?? "–"} hint={data?.latest ? `snapshot ${fmtDate(data.latest)}` : "no snapshot yet"} />
        <Tile label="Long-running" value={s?.long_running ?? "–"} hint={t ? `held ≥ ${t.warning_minutes} min` : undefined} />
        <Tile label="Critical" value={s?.critical ?? "–"} hint={t ? `held ≥ ${t.critical_minutes} min` : undefined} />
        <Tile label="Oldest lock" value={age(s?.oldest_minutes)} />
        <Tile label="Users holding locks" value={s?.users ?? "–"} hint={s ? `${s.tables} tables` : undefined} />
      </div>
      <Card title="Long-running locks">
        <DataTable<LockView> rows={data?.long_running ?? []} rowKey={(l, i) => `${l.user}-${l.table}-${i}`}
          empty={data?.latest ? "No lock is held longer than the warning threshold." : "No snapshot yet — upload an SM12 export below."}
          columns={lockColumns} />
        <p className="muted small">Before deleting a lock in SM12, check the owner's session (SM04/SM50) and any open update (SM13); deleting an active lock can corrupt data.</p>
      </Card>
      <div className="cols">
        <Card title="Lock count over time" actions={
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="History">
            {[1, 7, 30, 90].map((d) => <option key={d} value={d}>{d === 1 ? "24 hours" : `${d} days`}</option>)}
          </select>}>
          <LineChart points={(data?.history ?? []).map((h) => ({ t: Date.parse(h.captured_at), v: h.total }))} unit="locks" label="Lock count per snapshot" />
        </Card>
        <Card title="Who and where">
          <div className="cols">
            <DataTable rows={data?.by_user ?? []} rowKey={(u) => u.user} empty="–"
              columns={[{ key: "user", label: "User" }, { key: "locks", label: "Locks", numeric: true }]} />
            <DataTable rows={data?.by_table ?? []} rowKey={(x) => x.table} empty="–"
              columns={[{ key: "table", label: "Table" }, { key: "locks", label: "Locks", numeric: true }]} />
          </div>
        </Card>
      </div>
      {!!data?.persistent.length && (
        <Card title="Persistent locks (seen in several snapshots)">
          <DataTable<LockView> rows={data.persistent} rowKey={(l, i) => `${l.user}-${l.table}-p${i}`} columns={lockColumns} />
        </Card>
      )}
      <Card title="Upload SM12 snapshot">
        <form className="row" onSubmit={async (e) => {
          e.preventDefault();
          if (!csv && !window.confirm(`No file chosen. Record an empty lock list for ${system.sid} (no locks right now)?`)) return;
          try {
            const r = await api.uploadLocks(system.id, csv ?? "");
            setMessage({ ok: true, text: `Snapshot stored: ${r.locks} lock(s), ${r.long_running} long-running${r.long_running ? " — alerts sent" : ""}` });
            reload();
          } catch (err) { setMessage({ ok: false, text: (err as Error).message }); }
        }}>
          <FileField label="SM12 export (CSV)" accept=".csv,.txt" onText={(c) => setCsv(c as string | null)} />
          <button className="btn primary" type="submit">Upload snapshot</button>
        </form>
        <p className="muted small">Lock times are read in the system timezone ({system.timezone || "UTC"}); uploading with no file records an empty lock list.</p>
        <details><summary className="muted">How to export</summary><pre>{cap.how_to_export}</pre></details>
      </Card>
    </>
  );
}
