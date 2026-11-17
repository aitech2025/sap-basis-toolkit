import { useState } from "react";
import { api } from "../api";
import { useSession } from "../components/session";
import { Card, DataTable, ErrorBanner, Pill, fmtDate, useAsync } from "../components/ui";
import type { User } from "../types";

const ROLE_HELP = "viewer: read-only · operator: run checks, uploads, incidents, maintenance · admin: also collectors, rules, users, audit";

export default function Users() {
  const { can, me } = useSession();
  const { data, error, reload } = useAsync(() => (can("admin") ? api.users() : Promise.resolve([])), [me?.role]);
  const [form, setForm] = useState({ username: "", display_name: "", email: "", role: "operator", password: "" });
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  if (!can("admin")) return <div className="banner info">Only administrators can manage users.</div>;

  const run = async (fn: () => Promise<unknown>, text: string) => {
    setMessage(null);
    try { await fn(); setMessage({ ok: true, text }); reload(); } catch (e) { setMessage({ ok: false, text: (e as Error).message }); }
  };

  return (
    <>
      <div className="page-head"><div><h1>Users</h1><p>{ROLE_HELP}</p></div></div>
      <ErrorBanner error={error} />
      {message && <div className={`banner ${message.ok ? "ok" : "error"}`}>{message.text}</div>}
      <Card>
        <DataTable<User> rows={data ?? []} rowKey={(u) => u.id}
          columns={[
            { key: "username", label: "User", render: (u) => <><strong>{u.username}</strong><div className="muted small">{u.display_name}{u.email ? ` · ${u.email}` : ""}</div></> },
            { key: "role", label: "Role", render: (u) => (
              <select value={u.role} aria-label={`Role of ${u.username}`} onChange={(e) => run(() => api.updateUser(u.id, { role: e.target.value }), `${u.username} is now ${e.target.value}`)}>
                <option value="viewer">viewer</option><option value="operator">operator</option><option value="admin">admin</option>
              </select>) },
            { key: "active", label: "Status", render: (u) => <Pill value={u.active ? "enabled" : "disabled"} /> },
            { key: "last_login_at", label: "Last sign-in", render: (u) => fmtDate(u.last_login_at) },
            { key: "actions", label: "", render: (u) => (
              <div className="actions">
                <button className="btn small" onClick={() => run(() => api.updateUser(u.id, { active: !u.active }), `${u.username} ${u.active ? "disabled" : "enabled"}`)}>{u.active ? "Disable" : "Enable"}</button>
                <button className="btn small" onClick={() => {
                  const pw = window.prompt(`New password for ${u.username} (min. 12 characters). Their sessions will end.`);
                  if (pw) run(() => api.updateUser(u.id, { password: pw }), `Password reset for ${u.username}`);
                }}>Reset password</button>
              </div>) },
          ]} />
      </Card>
      <Card title="Add user">
        <form onSubmit={(e) => { e.preventDefault(); run(async () => { await api.createUser(form); setForm({ ...form, username: "", display_name: "", email: "", password: "" }); }, `User ${form.username} created`); }}>
          <div className="form-grid">
            <label className="field"><span>Username</span><input required value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></label>
            <label className="field"><span>Display name</span><input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></label>
            <label className="field"><span>Email</span><input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
            <label className="field"><span>Role</span>
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                <option value="viewer">viewer</option><option value="operator">operator</option><option value="admin">admin</option>
              </select></label>
            <label className="field"><span>Initial password</span><input type="password" autoComplete="new-password" required minLength={12} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /><small>At least 12 characters.</small></label>
          </div>
          <button className="btn primary" style={{ marginTop: 12 }} type="submit">Create user</button>
        </form>
      </Card>
    </>
  );
}
