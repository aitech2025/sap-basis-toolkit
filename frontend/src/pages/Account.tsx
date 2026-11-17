import { useState } from "react";
import { setToken } from "../api";
import { api } from "../api";
import { useSession } from "../components/session";
import { Card } from "../components/ui";

export default function Account() {
  const { me, logout } = useSession();
  const [form, setForm] = useState({ current: "", next: "", confirm: "" });
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  return (
    <>
      <div className="page-head"><div><h1>Account</h1><p>Signed in as <strong>{me?.username}</strong> ({me?.role}).</p></div>
        <button className="btn" onClick={logout}>Sign out</button></div>
      {me?.kind === "apikey" ? (
        <Card><p>You are using the server API key (admin service account). Create named users under <a href="/users">Users</a> so actions are attributed to people in the audit log.</p></Card>
      ) : (
        <Card title="Change password">
          {message && <div className={`banner ${message.ok ? "ok" : "error"}`}>{message.text}</div>}
          <form onSubmit={async (e) => {
            e.preventDefault();
            setMessage(null);
            if (form.next !== form.confirm) return setMessage({ ok: false, text: "New passwords do not match" });
            try {
              const r = await api.changePassword(form.current, form.next);
              setToken(r.token);
              setForm({ current: "", next: "", confirm: "" });
              setMessage({ ok: true, text: "Password changed. Other sessions have been signed out." });
            } catch (err) { setMessage({ ok: false, text: (err as Error).message }); }
          }}>
            <div className="form-grid">
              <label className="field"><span>Current password</span><input type="password" autoComplete="current-password" required value={form.current} onChange={(e) => setForm({ ...form, current: e.target.value })} /></label>
              <label className="field"><span>New password</span><input type="password" autoComplete="new-password" required minLength={12} value={form.next} onChange={(e) => setForm({ ...form, next: e.target.value })} /></label>
              <label className="field"><span>Confirm</span><input type="password" autoComplete="new-password" required value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} /></label>
            </div>
            <button className="btn primary" style={{ marginTop: 12 }} type="submit">Change password</button>
          </form>
        </Card>
      )}
    </>
  );
}
