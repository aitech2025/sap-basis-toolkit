import { Link } from "react-router-dom";
import { api } from "../api";
import { Card, DataTable, ErrorBanner, Pill, fmtDate, useAsync } from "../components/ui";
import type { AlertEvent } from "../types";

export default function Alerts() {
  const { data, error, reload } = useAsync(() => api.alerts(), []);
  return (
    <>
      <div className="page-head">
        <div><h1>Alerts</h1><p>Notifications sent to webhook, Slack, Teams, PagerDuty and email by all modules.</p></div>
        <button className="btn" onClick={reload}>Refresh</button>
      </div>
      <ErrorBanner error={error} />
      <Card>
        <DataTable<AlertEvent> rows={data ?? []} rowKey={(a) => a.id}
          empty="No alerts yet. Alerts appear here once a threshold is crossed and a channel is configured."
          columns={[
            { key: "sent_at", label: "When", render: (a) => fmtDate(a.sent_at) },
            { key: "sid", label: "System", render: (a) => a.system_id ? <Link to={`/systems/${a.system_id}/history`}>{a.sid}</Link> : "–" },
            { key: "severity", label: "Severity", render: (a) => <Pill value={a.severity} /> },
            { key: "module", label: "Module" },
            { key: "summary", label: "Alert" },
            { key: "channel", label: "Channel" },
            { key: "result", label: "Delivery", render: (a) => <span title={a.error ?? undefined}><Pill value={a.result} /></span> },
          ]} />
      </Card>
    </>
  );
}
