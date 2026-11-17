import { useState } from "react";
import { api } from "../api";
import { RequireRole } from "../components/session";
import { Card, DataTable, ErrorBanner, Pill, fmtDate, useAsync } from "../components/ui";
import type { MaintenanceWindow } from "../types";

const toLocalInput = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

export default function Maintenance() {
  const [past, setPast] = useState(false);
  const { data, error, reload } = useAsync(() => api.maintenance(past), [past]);
  const systems = useAsync(() => api.systems(), []);
  const now = new Date();
  const [form, setForm] = useState({ scope_type: "system", scope_value: "", starts_at: toLocalInput(now),
                                     ends_at: toLocalInput(new Date(now.getTime() + 2 * 3600000)), reason: "" });
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const landscapes = [...new Set((systems.data ?? []).map((s) => s.landscape))];
  const scope = (w: MaintenanceWindow) => w.scope_type === "global" ? "Everything"
    : w.scope_type === "landscape" ? `Landscape ${w.scope_value}`
    : `System ${systems.data?.find((s) => String(s.id) === w.scope_value)?.sid ?? w.scope_value}`;

  return (
    <>
      <div className="page-head">
        <div><h1>Maintenance windows</h1><p>Planned downtime: incidents are still recorded but notifications are suppressed.</p></div>
        <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={past} onChange={(e) => setPast(e.target.checked)} /> <span>Show past windows</span></label>
      </div>
      <ErrorBanner error={error} />
      {message && <div className={`banner ${message.ok ? "ok" : "error"}`}>{message.text}</div>}
      <Card>
        <DataTable<MaintenanceWindow> rows={data ?? []} rowKey={(w) => w.id} empty="No active or planned maintenance."
          columns={[
            { key: "state", label: "State", render: (w) => <Pill value={w.state === "active" ? "active" : w.state === "planned" ? "planned" : "past"} /> },
            { key: "scope", label: "Scope", render: scope },
            { key: "starts_at", label: "From", render: (w) => fmtDate(w.starts_at) },
            { key: "ends_at", label: "Until", render: (w) => fmtDate(w.ends_at) },
            { key: "reason", label: "Reason" },
            { key: "created_by", label: "By" },
            { key: "end", label: "", render: (w) => w.state !== "past" ? (
              <RequireRole role="operator">
                <button className="btn small" onClick={async () => { await api.endMaintenance(w.id); reload(); }}>{w.state === "active" ? "End now" : "Cancel"}</button>
              </RequireRole>) : null },
          ]} />
      </Card>
      <RequireRole role="operator">
        <Card title="Schedule maintenance">
          <form onSubmit={async (e) => {
            e.preventDefault();
            setMessage(null);
            try {
              await api.createMaintenance({ ...form, scope_value: form.scope_type === "global" ? null : form.scope_value,
                starts_at: new Date(form.starts_at).toISOString(), ends_at: new Date(form.ends_at).toISOString() });
              setMessage({ ok: true, text: "Maintenance window scheduled" });
              setForm({ ...form, reason: "" });
              reload();
            } catch (err) { setMessage({ ok: false, text: (err as Error).message }); }
          }}>
            <div className="form-grid">
              <label className="field"><span>Scope</span>
                <select value={form.scope_type} onChange={(e) => setForm({ ...form, scope_type: e.target.value, scope_value: "" })}>
                  <option value="system">One system</option><option value="landscape">A landscape</option><option value="global">Everything</option>
                </select></label>
              {form.scope_type !== "global" && (
                <label className="field"><span>{form.scope_type === "system" ? "System" : "Landscape"}</span>
                  <select required value={form.scope_value} onChange={(e) => setForm({ ...form, scope_value: e.target.value })}>
                    <option value="">Choose…</option>
                    {form.scope_type === "system"
                      ? systems.data?.map((s) => <option key={s.id} value={s.id}>{s.sid} · {s.landscape}</option>)
                      : landscapes.map((l) => <option key={l} value={l}>{l}</option>)}
                  </select></label>
              )}
              <label className="field"><span>From (your time)</span><input type="datetime-local" required value={form.starts_at} onChange={(e) => setForm({ ...form, starts_at: e.target.value })} /></label>
              <label className="field"><span>Until</span><input type="datetime-local" required value={form.ends_at} onChange={(e) => setForm({ ...form, ends_at: e.target.value })} /></label>
              <label className="field wide"><span>Reason</span><input required minLength={3} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="Kernel patch 793 PL 150, HANA revision upgrade…" /></label>
            </div>
            <button className="btn primary" style={{ marginTop: 12 }} type="submit">Schedule</button>
          </form>
        </Card>
      </RequireRole>
    </>
  );
}
