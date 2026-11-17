import { useState } from "react";
import { api } from "../../api";
import { LineChart } from "../../components/LineChart";
import { Card, DataTable, ErrorBanner, FileField, Tile, fmtNumber, useAsync } from "../../components/ui";
import type { GrowthTable, SystemDetail } from "../../types";

export default function GrowthTab({ system }: { system: SystemDetail }) {
  const [windowDays, setWindowDays] = useState(30);
  const { data, error, reload } = useAsync(() => api.growth(system.id, windowDays), [system.id, windowDays]);
  const [csv, setCsv] = useState<string | null>(null);
  const [capturedAt, setCapturedAt] = useState("");
  const [cap, setCap] = useState({ disk_total_gb: "", disk_used_gb: "", memory_total_gb: "", memory_used_gb: "" });
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [showTable, setShowTable] = useState(false);

  const act = async (fn: () => Promise<string>) => {
    setMessage(null);
    try {
      setMessage({ ok: true, text: await fn() });
      reload();
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    }
  };

  const f = data?.capacity_forecast;
  const maxRate = Math.max(1e-9, ...(data?.top_growing_tables ?? []).map((t) => t.growth_mb_per_day ?? 0));
  const sizePoints = (data?.size_history ?? []).map((p) => ({ t: Date.parse(p.captured_at), v: p.tracked_size_mb / 1024 }));
  const diskPoints = (data?.capacity_history ?? []).map((c) => ({ t: Date.parse(c.captured_at), v: (c.disk_used_mb / c.disk_total_mb) * 100 }));
  const isHana = system.database === "HANA";
  const num = (v: string) => (v === "" ? undefined : Number(v));

  return (
    <>
      <ErrorBanner error={error} />
      {message && <div className={`banner ${message.ok ? "ok" : "error"}`}>{message.text}</div>}
      <div className="tiles">
        <Tile label="Disk used" value={f ? `${f.disk_used_pct}%` : "–"} hint={f ? `alert threshold ${f.threshold_pct}%` : "record capacity below"} />
        <Tile label="Days to threshold" value={f?.days_until_disk_threshold ?? "–"} hint={f?.days_until_disk_full != null ? `${f.days_until_disk_full} days to full` : undefined} />
        {isHana && <Tile label="HANA memory used" value={f?.memory_used_pct != null ? `${f.memory_used_pct}%` : "–"} hint={f?.days_until_memory_threshold != null ? `${f.days_until_memory_threshold} days to threshold` : undefined} />}
        <Tile label="Tracked growth" value={`${fmtNumber(data?.tracked_growth_mb_per_day)} MB/day`} hint={`${data?.tracked_tables ?? 0} tables`} />
      </div>

      <div className="cols">
        <Card title="Tracked table size (GB)">
          <LineChart points={sizePoints} unit="GB" label="Tracked table size over time" />
        </Card>
        <Card title="Disk used (%)">
          <LineChart points={diskPoints} unit="%" label="Disk usage over time"
                     threshold={f ? { value: f.threshold_pct, label: `${f.threshold_pct}% threshold` } : undefined} />
        </Card>
      </div>

      <Card title="Fastest-growing tables" actions={
        <>
          <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <span>Window</span>
            <select value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))}>
              {[7, 30, 90, 180, 365].map((d) => <option key={d} value={d}>{d} days</option>)}
            </select>
          </label>
          <button className="btn small" onClick={() => setShowTable(!showTable)}>{showTable ? "Hide" : "Show"} chart data</button>
        </>
      }>
        <DataTable<GrowthTable> rows={data?.top_growing_tables ?? []} rowKey={(t) => t.table_name}
          empty="No snapshots yet. Upload a table-size export below."
          columns={[
            { key: "table_name", label: "Table", render: (t) => <strong>{t.table_name}</strong> },
            { key: "size_mb", label: "Size MB", numeric: true, render: (t) => fmtNumber(t.size_mb) },
            { key: "growth_mb_per_day", label: "MB / day", numeric: true, render: (t) => (
              <div className="bar-cell">
                <span>{fmtNumber(t.growth_mb_per_day, 2)}</span>
                <span className="bar" style={{ width: `${Math.max(0, (t.growth_mb_per_day ?? 0) / maxRate) * 80}px` }} />
              </div>) },
            { key: "growth_pct_window", label: "Growth %", numeric: true, render: (t) => t.growth_pct_window ?? "–" },
            { key: "row_count", label: "Rows", numeric: true, render: (t) => fmtNumber(t.row_count, 0) },
            { key: "recommendation", label: "Housekeeping", render: (t) => t.recommendation ?? <span className="muted">–</span> },
          ]} />
        {showTable && (
          <div className="cols" style={{ marginTop: 12 }}>
            <DataTable rows={data?.size_history ?? []} rowKey={(r) => r.captured_at}
              columns={[{ key: "captured_at", label: "Snapshot" }, { key: "tracked_size_mb", label: "Tracked MB", numeric: true }]} />
            <DataTable rows={data?.capacity_history ?? []} rowKey={(r) => r.captured_at}
              columns={[{ key: "captured_at", label: "Captured" }, { key: "disk_used_mb", label: "Disk used MB", numeric: true },
                        { key: "disk_total_mb", label: "Disk total MB", numeric: true }]} />
          </div>
        )}
      </Card>

      <div className="cols">
        <Card title="Upload table-size snapshot">
          <form onSubmit={(e) => { e.preventDefault(); if (!csv) return setMessage({ ok: false, text: "Choose a CSV export" });
            act(async () => { const r = await api.uploadSnapshot(system.id, csv, capturedAt ? new Date(capturedAt).toISOString() : undefined);
              return `Stored ${r.tables} tables (${fmtNumber(r.total_size_mb)} MB)`; }); }}>
            <div className="form-grid">
              <FileField label={`${system.database} export (CSV)`} accept=".csv,.txt" onText={(c) => setCsv(c as string | null)} />
              <label className="field"><span>Captured at</span><input type="datetime-local" value={capturedAt} onChange={(e) => setCapturedAt(e.target.value)} /><small>Leave empty for now.</small></label>
            </div>
            <button className="btn primary" style={{ marginTop: 12 }} type="submit">Upload</button>
          </form>
          <details style={{ marginTop: 12 }}><summary className="muted">How to export from {system.database}</summary><pre>{system.capabilities.growth.how_to_export}</pre></details>
        </Card>
        <Card title="Record capacity">
          <form onSubmit={(e) => { e.preventDefault(); act(async () => {
            await api.recordCapacity(system.id, { disk_total_gb: num(cap.disk_total_gb), disk_used_gb: num(cap.disk_used_gb),
              memory_total_gb: num(cap.memory_total_gb), memory_used_gb: num(cap.memory_used_gb) });
            return "Capacity recorded"; }); }}>
            <div className="form-grid">
              <label className="field"><span>Data volume total (GB)</span><input required type="number" step="any" min="0" value={cap.disk_total_gb} onChange={(e) => setCap({ ...cap, disk_total_gb: e.target.value })} /></label>
              <label className="field"><span>Data volume used (GB)</span><input required type="number" step="any" min="0" value={cap.disk_used_gb} onChange={(e) => setCap({ ...cap, disk_used_gb: e.target.value })} /></label>
              {isHana && <>
                <label className="field"><span>Memory allocation limit (GB)</span><input type="number" step="any" min="0" value={cap.memory_total_gb} onChange={(e) => setCap({ ...cap, memory_total_gb: e.target.value })} /></label>
                <label className="field"><span>Memory used (GB)</span><input type="number" step="any" min="0" value={cap.memory_used_gb} onChange={(e) => setCap({ ...cap, memory_used_gb: e.target.value })} /></label>
              </>}
            </div>
            <button className="btn primary" style={{ marginTop: 12 }} type="submit">Save</button>
          </form>
        </Card>
      </div>
    </>
  );
}
