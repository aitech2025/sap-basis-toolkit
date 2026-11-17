import { useState } from "react";
import { api } from "../api";
import { RequireRole, useSession } from "../components/session";
import { Card, DataTable, ErrorBanner, Pill, useAsync } from "../components/ui";
import type { Rule } from "../types";

function Threshold({ rule, field, editable, onSave }: { rule: Rule; field: "warning" | "critical"; editable: boolean; onSave: (v: number | null) => void }) {
  const [value, setValue] = useState(rule[field] == null ? "" : String(rule[field]));
  if (!editable) return <>{rule[field] == null ? "–" : `${rule.operator} ${rule[field]}`}</>;
  return (
    <input className="num-input" aria-label={`${rule.name} ${field}`} value={value} inputMode="decimal"
           onChange={(e) => setValue(e.target.value)}
           onBlur={() => { const v = value.trim() === "" ? null : Number(value); if (v !== rule[field] && (v === null || !Number.isNaN(v))) onSave(v); }} />
  );
}

export default function Rules() {
  const { can } = useSession();
  const admin = can("admin");
  const { data, error, reload } = useAsync(() => api.rules(), []);
  const metrics = useAsync(() => api.ruleMetrics(), []);
  const systems = useAsync(() => api.systems(), []);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [form, setForm] = useState({ metric: "", scope_type: "system", scope_value: "", warning: "", critical: "" });

  const save = async (fn: () => Promise<unknown>, text: string) => {
    setMessage(null);
    try { await fn(); setMessage({ ok: true, text }); reload(); } catch (e) { setMessage({ ok: false, text: (e as Error).message }); }
  };
  const scopeLabel = (r: Rule) => r.scope_type === "global" ? "All systems"
    : r.scope_type === "landscape" ? `Landscape ${r.scope_value}`
    : `System ${systems.data?.find((s) => String(s.id) === r.scope_value)?.sid ?? r.scope_value}`;
  const landscapes = [...new Set((systems.data ?? []).map((s) => s.landscape))];
  const metricInfo = metrics.data?.find((m) => m.metric === form.metric);

  return (
    <>
      <div className="page-head"><div><h1>Alert rules</h1>
        <p>Thresholds that turn collector metrics into incidents. The most specific enabled rule wins: system → landscape → global.</p></div></div>
      <ErrorBanner error={error} />
      {message && <div className={`banner ${message.ok ? "ok" : "error"}`}>{message.text}</div>}
      {!admin && <div className="banner info">Only administrators can change rules.</div>}
      <Card>
        <DataTable<Rule> rows={data ?? []} rowKey={(r) => r.id}
          columns={[
            { key: "enabled", label: "On", render: (r) => admin
              ? <input type="checkbox" checked={r.enabled} aria-label={`Enable ${r.name}`} onChange={(e) => save(() => api.updateRule(r.id, { enabled: e.target.checked }), `${r.name} ${e.target.checked ? "enabled" : "disabled"}`)} />
              : <Pill value={r.enabled ? "enabled" : "disabled"} /> },
            { key: "name", label: "Rule", render: (r) => <><strong>{r.name}</strong><div className="muted small">{r.description}</div></> },
            { key: "metric", label: "Metric", render: (r) => <code>{r.metric}</code> },
            { key: "scope", label: "Applies to", render: scopeLabel },
            { key: "warning", label: "Warning", render: (r) => <Threshold rule={r} field="warning" editable={admin} onSave={(v) => save(() => api.updateRule(r.id, { warning: v }), "Threshold saved")} /> },
            { key: "critical", label: "Critical", render: (r) => <Threshold rule={r} field="critical" editable={admin} onSave={(v) => save(() => api.updateRule(r.id, { critical: v }), "Threshold saved")} /> },
            { key: "del", label: "", render: (r) => admin && !r.builtin
              ? <button className="btn small danger" onClick={() => window.confirm(`Delete rule ${r.name}?`) && save(() => api.deleteRule(r.id), "Rule deleted")}>Delete</button> : null },
          ]} />
      </Card>
      <RequireRole role="admin">
        <Card title="Override a rule for a landscape or system">
          <form className="row" onSubmit={(e) => {
            e.preventDefault();
            const base = metrics.data?.find((m) => m.metric === form.metric);
            if (!base) return;
            const num = (v: string) => (v.trim() === "" ? null : Number(v));
            save(() => api.createRule({ name: `${base.name} (${form.scope_type} override)`, metric: base.metric, operator: base.operator,
              warning: num(form.warning), critical: num(form.critical), scope_type: form.scope_type, scope_value: form.scope_value,
              description: base.description }), "Override created");
          }}>
            <label className="field"><span>Metric</span>
              <select required value={form.metric} onChange={(e) => setForm({ ...form, metric: e.target.value })}>
                <option value="">Choose…</option>
                {metrics.data?.map((m) => <option key={m.metric} value={m.metric}>{m.name}</option>)}
              </select></label>
            <label className="field"><span>Scope</span>
              <select value={form.scope_type} onChange={(e) => setForm({ ...form, scope_type: e.target.value, scope_value: "" })}>
                <option value="system">System</option><option value="landscape">Landscape</option>
              </select></label>
            <label className="field"><span>{form.scope_type === "system" ? "System" : "Landscape"}</span>
              <select required value={form.scope_value} onChange={(e) => setForm({ ...form, scope_value: e.target.value })}>
                <option value="">Choose…</option>
                {form.scope_type === "system"
                  ? systems.data?.map((s) => <option key={s.id} value={s.id}>{s.sid} · {s.landscape}</option>)
                  : landscapes.map((l) => <option key={l} value={l}>{l}</option>)}
              </select></label>
            <label className="field"><span>Warning {metricInfo ? `(${metricInfo.operator})` : ""}</span><input className="num-input" value={form.warning} onChange={(e) => setForm({ ...form, warning: e.target.value })} placeholder={String(metricInfo?.warning ?? "")} /></label>
            <label className="field"><span>Critical {metricInfo ? `(${metricInfo.operator})` : ""}</span><input className="num-input" value={form.critical} onChange={(e) => setForm({ ...form, critical: e.target.value })} placeholder={String(metricInfo?.critical ?? "")} /></label>
            <button className="btn primary" type="submit">Create override</button>
          </form>
        </Card>
      </RequireRole>
    </>
  );
}
