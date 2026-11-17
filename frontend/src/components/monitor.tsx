import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { MonitorDetail, SystemDetail } from "../types";
import { LineChart } from "./LineChart";
import { RequireRole } from "./session";
import { Card, Empty, FileField, Pill, fmtDate, useAsync } from "./ui";

export const SOURCE_LABEL: Record<string, string> = {
  sapcontrol: "SAP Start Service", hana: "HANA SQL", upload: "uploaded export", simulator: "demo simulator",
};

export function useMonitor(system: SystemDetail, monitor: string, hours = 24) {
  return useAsync(() => api.monitor(system.id, monitor, hours), [system.id, monitor, hours]);
}

export function Freshness({ detail }: { detail: MonitorDetail | null }) {
  if (!detail?.latest) return null;
  const l = detail.latest;
  return (
    <span className="muted small">
      <Pill value={l.health} /> {SOURCE_LABEL[l.source] ?? l.source} · {fmtDate(l.captured_at)}
      {l.stale && <strong className="text-warn"> · stale</strong>}
    </span>
  );
}

export function NoData({ system, what, hint }: { system: SystemDetail; what: string; hint: ReactNode }) {
  return (
    <Empty>
      No {what} data yet. {hint} <Link to={`/systems/${system.id}/setup`}>Set up a live collector</Link>.
    </Empty>
  );
}

export function Trend({ points, unit, label, threshold }: {
  points: { ts: string; value: number }[] | undefined; unit: string; label: string; threshold?: { value: number; label: string };
}) {
  return <LineChart points={(points ?? []).map((p) => ({ t: Date.parse(p.ts), v: p.value }))} unit={unit} label={label} threshold={threshold} height={150} />;
}

export function UploadFallback({ system, monitor, label, onDone, how }: {
  system: SystemDetail; monitor: string; label: string; onDone: () => void; how: string;
}) {
  const [csv, setCsv] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  return (
    <RequireRole role="operator">
      <details className="upload-fallback">
        <summary>Upload a {label} export instead (systems without a HANA collector)</summary>
        {message && <div className={`banner ${message.ok ? "ok" : "error"}`}>{message.text}</div>}
        <form className="row" onSubmit={async (e) => {
          e.preventDefault();
          if (!csv) return setMessage({ ok: false, text: "Choose a file" });
          try {
            const r = await api.uploadMonitor(system.id, monitor, csv);
            setMessage({ ok: true, text: `Stored — health ${r.health}` });
            onDone();
          } catch (err) { setMessage({ ok: false, text: (err as Error).message }); }
        }}>
          <FileField label={`${label} export (CSV)`} accept=".csv,.txt" onText={(c) => setCsv(c as string | null)} />
          <button className="btn" type="submit">Upload</button>
        </form>
        <p className="muted small">{how}</p>
      </details>
    </RequireRole>
  );
}

export function MonitorCard({ title, detail, children }: { title: string; detail: MonitorDetail | null; children: ReactNode }) {
  return <Card title={<>{title} <Freshness detail={detail} /></>}>{children}</Card>;
}
