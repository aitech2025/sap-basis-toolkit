import { api } from "../../api";
import { Card, DataTable, ErrorBanner, Pill, fmtDate, useAsync } from "../../components/ui";
import type { AlertEvent, Run, SystemDetail } from "../../types";

const MODULE_LABEL: Record<string, string> = {
  users: "User & license analysis", jobs: "Background job upload", "transports-precheck": "Transport pre-check", "transports-postcheck": "Import post-check",
};

export default function HistoryTab({ system }: { system: SystemDetail }) {
  const runs = useAsync(() => api.runs(system.id), [system.id]);
  const alerts = useAsync(() => api.alerts(system.id), [system.id]);
  return (
    <>
      <ErrorBanner error={runs.error || alerts.error} />
      <Card title="Analysis runs">
        <p className="muted">Summary counts only — uploaded exports and user names are never stored.</p>
        <DataTable<Run> rows={runs.data ?? []} rowKey={(r) => r.id} empty="No analyses yet."
          columns={[
            { key: "created_at", label: "When", render: (r) => fmtDate(r.created_at) },
            { key: "module", label: "Analysis", render: (r) => MODULE_LABEL[r.module] ?? r.module },
            { key: "status", label: "Result", render: (r) => <Pill value={r.status} /> },
            { key: "summary", label: "Summary", render: (r) => Object.entries(r.summary).map(([k, v]) => `${k.replaceAll("_", " ")}: ${v}`).join(" · ") },
          ]} />
      </Card>
      <Card title="Alerts">
        <DataTable<AlertEvent> rows={alerts.data ?? []} rowKey={(a) => a.id} empty="No alerts sent for this system."
          columns={[
            { key: "sent_at", label: "When", render: (a) => fmtDate(a.sent_at) },
            { key: "severity", label: "Severity", render: (a) => <Pill value={a.severity} /> },
            { key: "summary", label: "Alert" },
            { key: "channel", label: "Channel" },
            { key: "result", label: "Delivery", render: (a) => <Pill value={a.result} /> },
          ]} />
      </Card>
    </>
  );
}
