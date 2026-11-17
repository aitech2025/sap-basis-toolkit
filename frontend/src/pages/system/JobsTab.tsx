import { useState } from "react";
import { api } from "../../api";
import { Card, DataTable, Empty, ErrorBanner, FileField, Pill, Tile, fmtDate, useAsync } from "../../components/ui";
import type { FailingJob, SystemDetail } from "../../types";

const fmtDuration = (s: number | null | undefined) => {
  if (s == null) return "–";
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
};

export default function JobsTab({ system }: { system: SystemDetail }) {
  const cap = system.capabilities.jobs;
  const [days, setDays] = useState(7);
  const { data, error, reload } = useAsync(() => (cap.supported ? api.jobsReport(system.id, days) : Promise.resolve(null)), [system.id, days]);
  const [csv, setCsv] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  if (!cap.supported) return <Card title="Background jobs"><Empty>{cap.how_to_export}</Empty></Card>;
  const s = data?.summary;

  return (
    <>
      <ErrorBanner error={error} />
      {message && <div className={`banner ${message.ok ? "ok" : "error"}`}>{message.text}</div>}
      {data?.stale && data.last_capture && <div className="banner info">Job data is from {fmtDate(data.last_capture)} — upload a fresh SM37 export for current status.</div>}
      <div className="tiles">
        <Tile label={`Runs (${days} days)`} value={s?.runs ?? "–"} hint={s ? `${s.finished} finished · ${s.active} active` : undefined} />
        <Tile label="Cancelled" value={s?.cancelled ?? "–"} hint={s ? `${s.cancelled_24h} in last 24 h` : undefined} />
        <Tile label="Failure rate" value={s?.failure_rate_pct != null ? `${s.failure_rate_pct}%` : "–"} hint="of completed runs" />
        <Tile label="Failing jobs" value={s?.failing_jobs ?? "–"} />
        <Tile label="Long-running" value={s?.long_running ?? "–"} hint={s ? `active > ${s.long_running_threshold_minutes} min` : undefined} />
      </div>
      <Card title="Failing jobs" actions={
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Window">
          {[1, 7, 14, 30, 90].map((d) => <option key={d} value={d}>{d === 1 ? "Last 24 hours" : `Last ${d} days`}</option>)}
        </select>}>
        <DataTable<FailingJob> rows={data?.failing_jobs ?? []} rowKey={(j) => j.job_name}
          empty={data?.last_capture ? "No cancelled jobs in this window." : "No job data yet — upload an SM37 or TBTCO export below."}
          columns={[
            { key: "job_name", label: "Job", render: (j) => <strong>{j.job_name}</strong> },
            { key: "state", label: "Now", render: (j) => j.still_failing ? <Pill value="failed" /> : <Pill value="recovered" /> },
            { key: "cancelled", label: "Cancelled", numeric: true },
            { key: "runs", label: "Runs", numeric: true },
            { key: "failure_rate_pct", label: "Failure %", numeric: true, render: (j) => j.failure_rate_pct ?? "–" },
            { key: "last_failure", label: "Last failure", render: (j) => fmtDate(j.last_failure) },
            { key: "scheduled_by", label: "Scheduled by", render: (j) => j.scheduled_by ?? "–" },
          ]} />
      </Card>
      <div className="cols">
        <Card title="Long-running active jobs">
          <DataTable rows={data?.long_running ?? []} rowKey={(j) => `${j.job_name}-${j.job_count}`}
            empty="No active job exceeds the threshold."
            columns={[
              { key: "job_name", label: "Job" },
              { key: "start_at", label: "Started", render: (j) => fmtDate(j.start_at) },
              { key: "running_minutes", label: "Running", numeric: true, render: (j) => fmtDuration(j.running_minutes * 60) },
              { key: "scheduled_by", label: "By", render: (j) => j.scheduled_by ?? "–" },
            ]} />
        </Card>
        <Card title="Recent cancellations">
          <DataTable rows={data?.recent_failures ?? []} rowKey={(j, i) => `${j.job_name}-${j.job_count}-${i}`}
            empty="No cancellations."
            columns={[
              { key: "job_name", label: "Job" },
              { key: "start_at", label: "Started", render: (j) => fmtDate(j.start_at) },
              { key: "duration_s", label: "Ran for", numeric: true, render: (j) => fmtDuration(j.duration_s) },
              { key: "job_count", label: "Count", render: (j) => j.job_count ?? "–" },
            ]} />
        </Card>
      </div>
      <Card title="Upload SM37 / TBTCO export">
        <form className="row" onSubmit={async (e) => {
          e.preventDefault();
          if (!csv) return setMessage({ ok: false, text: "Choose an export file" });
          try {
            const r = await api.uploadJobs(system.id, csv);
            setMessage({ ok: true, text: `${r.rows} job runs read: ${r.inserted} new, ${r.updated} updated, ${r.new_failures} new cancellation(s)${r.new_failures ? " — alerts sent" : ""}` });
            reload();
          } catch (err) { setMessage({ ok: false, text: (err as Error).message }); }
        }}>
          <FileField label="Export (CSV)" accept=".csv,.txt" onText={(c) => setCsv(c as string | null)} />
          <button className="btn primary" type="submit">Upload</button>
        </form>
        <p className="muted small">Times are read in the system timezone ({system.timezone || "UTC"}).</p>
        <details><summary className="muted">How to export</summary><pre>{cap.how_to_export}</pre></details>
      </Card>
    </>
  );
}
