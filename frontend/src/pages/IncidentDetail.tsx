import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { RequireRole } from "../components/session";
import { Card, ErrorBanner, Pill, fmtDate, useAsync } from "../components/ui";
import { ago } from "./Incidents";

const KIND_LABEL: Record<string, string> = {
  opened: "Opened", escalated: "Escalated", deescalated: "Severity lowered", acknowledged: "Acknowledged", resolved: "Resolved",
  auto_resolved: "Resolved automatically", reopened: "Reopened", assigned: "Assignment", comment: "Comment",
  notified: "Notification sent", notify_failed: "Notification failed", suppressed: "Notification suppressed",
};

export default function IncidentDetail() {
  const id = Number(useParams().id);
  const { data, error, reload, setData } = useAsync(() => api.incident(id), [id]);
  const [message, setMessage] = useState("");
  const [assignee, setAssignee] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  const act = async (action: string, body: { message?: string; assignee?: string | null } = {}) => {
    setActionError(null);
    try {
      setData(await api.incidentAction(id, action, body));
      setMessage("");
    } catch (e) {
      setActionError((e as Error).message);
      reload();
    }
  };

  if (error) return <ErrorBanner error={error} />;
  if (!data) return <p className="muted">Loading…</p>;
  const details = Object.entries(data.details ?? {}).filter(([, v]) => v !== null && v !== "");

  return (
    <>
      <div className="page-head">
        <div>
          <p><Link to="/incidents">Incidents</Link> · #{data.id}</p>
          <h1>{data.title}</h1>
          <p><Pill value={data.severity} /> <Pill value={data.status === "resolved" ? "resolved" : data.status} />
            {data.system_id && <> · <Link to={`/systems/${data.system_id}`}>{data.sid}</Link> ({data.landscape})</>}
            {data.in_maintenance && <> · <span className="tag">opened during maintenance</span></>}</p>
        </div>
        <RequireRole role="operator">
          <div className="actions">
            {data.status === "open" && <button className="btn primary" onClick={() => act("acknowledge", { message })}>Acknowledge</button>}
            {data.status !== "resolved" && <button className="btn" onClick={() => act("resolve", { message: message || "Resolved" })}>Resolve</button>}
            {data.status === "resolved" && <button className="btn" onClick={() => act("reopen", { message })}>Reopen</button>}
          </div>
        </RequireRole>
      </div>
      <ErrorBanner error={actionError} />
      <div className="cols">
        <Card title="Details">
          <table><tbody>
            <tr><th>Source</th><td>{data.source === "rule" ? `Alert rule on ${data.metric}` : "Module alert"}</td></tr>
            <tr><th>Opened</th><td>{fmtDate(data.opened_at)} ({ago(data.opened_at)})</td></tr>
            <tr><th>Last seen</th><td>{fmtDate(data.last_seen_at)} · {data.occurrences}×</td></tr>
            {data.value != null && <tr><th>Current value</th><td>{data.value}</td></tr>}
            <tr><th>Owner</th><td>{data.assignee ?? "–"}</td></tr>
            {data.acknowledged_by && <tr><th>Acknowledged</th><td>{data.acknowledged_by} · {fmtDate(data.acknowledged_at)}</td></tr>}
            {data.resolved_by && <tr><th>Resolved</th><td>{data.resolved_by} · {fmtDate(data.resolved_at)}{data.resolution ? ` — ${data.resolution}` : ""}</td></tr>}
            {details.map(([k, v]) => <tr key={k}><th>{k.replaceAll("_", " ")}</th><td>{String(v)}</td></tr>)}
          </tbody></table>
        </Card>
        <RequireRole role="operator" fallback={<Card title="Actions"><p className="muted">Viewers can follow incidents; operators acknowledge and resolve them.</p></Card>}>
          <Card title="Work on this incident">
            <label className="field"><span>Note (added to the timeline with the next action)</span>
              <textarea rows={3} value={message} onChange={(e) => setMessage(e.target.value)} placeholder="What did you find / do?" /></label>
            <div className="actions" style={{ marginTop: 8 }}>
              <button className="btn" disabled={!message.trim()} onClick={() => act("comment", { message })}>Add comment</button>
            </div>
            <div className="row" style={{ marginTop: 16 }}>
              <label className="field"><span>Assign to (username)</span><input value={assignee} onChange={(e) => setAssignee(e.target.value)} placeholder={data.assignee ?? "e.g. olga"} /></label>
              <button className="btn" onClick={() => act("assign", { assignee: assignee || null })}>{assignee ? "Assign" : "Unassign"}</button>
            </div>
          </Card>
        </RequireRole>
      </div>
      <Card title="Timeline">
        <ol className="timeline">
          {data.timeline?.map((e) => (
            <li key={e.id} className={`tl-${e.kind}`}>
              <span className="tl-at">{fmtDate(e.at)}</span>
              <span className="tl-kind">{KIND_LABEL[e.kind] ?? e.kind}</span>
              <span className="tl-actor">{e.actor}</span>
              {e.message && <span className="tl-msg">{e.message}</span>}
            </li>
          ))}
        </ol>
      </Card>
    </>
  );
}
