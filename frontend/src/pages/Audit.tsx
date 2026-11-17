import { useState } from "react";
import { api } from "../api";
import { useSession } from "../components/session";
import { Card, DataTable, ErrorBanner, fmtDate, useAsync } from "../components/ui";
import type { AuditEntry } from "../types";

export default function Audit() {
  const { can, me } = useSession();
  const [user, setUser] = useState("");
  const [action, setAction] = useState("");
  const query = `?limit=500${user ? `&username=${encodeURIComponent(user)}` : ""}${action ? `&action=${encodeURIComponent(action)}` : ""}`;
  const { data, error } = useAsync(() => (can("admin") ? api.audit(query) : Promise.resolve([])), [query, me?.role]);
  if (!can("admin")) return <div className="banner info">Only administrators can read the audit log.</div>;
  return (
    <>
      <div className="page-head">
        <div><h1>Audit log</h1><p>Every sign-in and every change made through the UI or API, with the acting user.</p></div>
        <div className="actions">
          <input placeholder="Username" value={user} onChange={(e) => setUser(e.target.value)} aria-label="Filter by user" />
          <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Filter by action">
            <option value="">All actions</option><option value="login">Sign-ins</option><option value="POST">Creates / actions</option>
            <option value="PATCH">Changes</option><option value="DELETE">Deletions</option>
          </select>
        </div>
      </div>
      <ErrorBanner error={error} />
      <Card>
        <DataTable<AuditEntry> rows={data ?? []} rowKey={(a) => a.id}
          columns={[
            { key: "at", label: "When", render: (a) => fmtDate(a.at) },
            { key: "username", label: "User" },
            { key: "action", label: "Action", render: (a) => <code>{a.action}</code> },
            { key: "status", label: "Result", render: (a) => a.status == null ? "–" : <span className={a.status >= 400 ? "text-bad" : ""}>{a.status}</span> },
            { key: "ip", label: "From", render: (a) => a.ip ?? "–" },
          ]} />
      </Card>
    </>
  );
}
