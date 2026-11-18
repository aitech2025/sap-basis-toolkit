import { useState } from "react";
import { api } from "../api";
import type { Collector, SystemDetail } from "../types";
import { RequireRole, useSession } from "./session";
import { Card, DataTable, Empty, Pill, fmtDate, useAsync } from "./ui";

const DEFAULTS = {
  sapcontrol: { port: 50014, interval_seconds: 120, username: "", abap_schema: "" },
  hana: { port: 30015, interval_seconds: 300, username: "", abap_schema: "SAPHANADB" },
  node_exporter: { port: 9100, interval_seconds: 60, username: "", abap_schema: "", use_tls: false, verify_tls: false },
  hana_sapcontrol: { port: 50013, interval_seconds: 120, username: "", abap_schema: "", use_tls: false, verify_tls: false },
  os_ssh: { port: 22, interval_seconds: 900, username: "root", abap_schema: "", use_tls: false, verify_tls: false },
};
type CollectorType = keyof typeof DEFAULTS;
const TYPE_LABEL: Record<string, string> = { sapcontrol: "SAP Start Service", hana: "HANA SQL", hana_sapcontrol: "HANA instance (sapcontrol)", node_exporter: "node_exporter", os_ssh: "OS inventory (SSH)" };

export function CollectorsCard({ system }: { system: SystemDetail }) {
  const { can } = useSession();
  const { data, error, reload } = useAsync(() => api.collectors(system.id), [system.id]);
  const [type, setType] = useState<CollectorType>("sapcontrol");
  const [form, setForm] = useState({ host: system.host ?? "", port: 50014, use_tls: true, verify_tls: true, username: "",
                                     password: "", abap_schema: "", interval_seconds: 120 });
  const [message, setMessage] = useState<{ ok: boolean; text: string; details?: string[] } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const pickType = (t: CollectorType) => {
    setType(t);
    setForm({ ...form, ...DEFAULTS[t] });
  };
  const body = () => ({ type, ...form, port: Number(form.port), interval_seconds: Number(form.interval_seconds),
                        username: form.username || null, password: form.password || null, abap_schema: form.abap_schema || null });
  const run = async (key: string, fn: () => Promise<{ text: string; ok?: boolean; details?: string[] }>) => {
    setBusy(key);
    setMessage(null);
    try { const r = await fn(); setMessage({ ok: r.ok ?? true, text: r.text, details: r.details }); reload(); }
    catch (e) { setMessage({ ok: false, text: (e as Error).message }); }
    finally { setBusy(null); }
  };

  return (
    <Card title="Live collectors">
      <p className="muted small">
        The SAP Start Service (sapcontrol, ports 5&lt;NN&gt;13/14) provides availability, processes, work processes and the
        system log for ABAP and Java. A HANA SQL collector (read-only monitoring user) adds HANA health and backups, and — with the
        ABAP schema — short dumps, update errors, tRFC/qRFC, IDocs, background jobs, import history and table growth.
        The node_exporter collector adds host load, memory and root-filesystem utilisation. Hosts must be allowed by the server's outbound policy.
      </p>
      {error && <div className="banner error">{error}</div>}
      {message && (
        <div className={`banner ${message.ok ? "ok" : "error"}`}>
          {message.text}
          {message.details && <ul className="small">{message.details.map((d) => <li key={d}>{d}</li>)}</ul>}
        </div>
      )}
      {data && !data.length && <Empty>No collectors yet — data comes only from uploads until you add one.</Empty>}
      {!!data?.length && (
        <DataTable<Collector> rows={data} rowKey={(c) => c.id} columns={[
          { key: "type", label: "Type", render: (c) => <><strong>{TYPE_LABEL[c.type] ?? c.type}</strong>{c.simulated && <span className="tag">demo</span>}</> },
          { key: "host", label: "Endpoint", render: (c) => <code>{c.use_tls ? "https" : "http"}://{c.host}:{c.port}</code> },
          { key: "user", label: "User", render: (c) => c.username ? `${c.username}${c.has_password ? " (password set)" : ""}` : <span className="muted">–</span> },
          { key: "abap_schema", label: "ABAP schema", render: (c) => c.abap_schema ?? "–" },
          { key: "interval", label: "Every", render: (c) => `${Math.round(c.interval_seconds / 60 * 10) / 10} min` },
          { key: "status", label: "Last run", render: (c) => c.last_status ? (
            <span title={c.last_error ?? ""}><Pill value={c.last_status === "ok" ? "healthy" : c.last_status === "partial" ? "warning" : "critical"} /> {fmtDate(c.last_run_at)}
              {c.last_duration_ms != null && <span className="muted small"> · {c.last_duration_ms} ms</span>}</span>) : <span className="muted">never</span> },
          { key: "enabled", label: "", render: (c) => c.enabled ? null : <Pill value="disabled" /> },
          { key: "actions", label: "", render: (c) => (
            <div className="actions">
              <RequireRole role="operator">
                <button className="btn small" disabled={busy === `run${c.id}`} onClick={() => run(`run${c.id}`, async () => {
                  const r = await api.runCollector(c.id);
                  return { ok: r.status !== "error", text: `Run ${r.status} in ${r.duration_ms} ms${r.error ? `: ${r.error}` : ""}` };
                })}>{busy === `run${c.id}` ? "Running…" : "Run now"}</button>
              </RequireRole>
              <RequireRole role="admin">
                <button className="btn small" onClick={() => run(`t${c.id}`, async () => {
                  await api.updateCollector(c.id, { enabled: !c.enabled });
                  return { text: `Collector ${c.enabled ? "paused" : "enabled"}` };
                })}>{c.enabled ? "Pause" : "Enable"}</button>
                <button className="btn small" onClick={() => {
                  const pw = window.prompt("New password (stored encrypted; leave empty to keep):");
                  if (pw) run(`p${c.id}`, async () => { await api.updateCollector(c.id, { password: pw }); return { text: "Password updated" }; });
                }}>Password</button>
                <button className="btn small danger" onClick={() => window.confirm("Delete this collector? Collected history is kept.") &&
                  run(`d${c.id}`, async () => { await api.deleteCollector(c.id); return { text: "Collector deleted" }; })}>Delete</button>
              </RequireRole>
            </div>) },
        ]} />
      )}
      {data?.some((c) => c.diagnostics.some((d) => !d.ok)) && (
        <details style={{ marginTop: 8 }}>
          <summary className="muted">Diagnostics of the last runs</summary>
          <ul className="small">
            {data.flatMap((c) => c.diagnostics.filter((d) => !d.ok).map((d, i) => (
              <li key={`${c.id}-${i}`}><strong>{c.type}</strong> {d.instance ? `${d.instance} ` : ""}<code>{d.call}</code>: {d.error}{d.experimental ? " (query marked 'verify on your release')" : ""}</li>)))}
          </ul>
        </details>
      )}
      <RequireRole role="admin">
        <form className="collector-form" onSubmit={(e) => {
          e.preventDefault();
          run("add", async () => { await api.createCollector(system.id, body()); return { text: "Collector added — first run starts within a minute" }; });
        }}>
          <h3>Add collector</h3>
          <div className="form-grid">
            <label className="field"><span>Type</span>
              <select value={type} onChange={(e) => pickType(e.target.value as CollectorType)}>
                <option value="sapcontrol">SAP Start Service (sapcontrol)</option>
                <option value="hana" disabled={system.database !== "HANA"}>HANA SQL{system.database !== "HANA" ? " (HANA systems only)" : ""}</option>
                <option value="hana_sapcontrol" disabled={system.database !== "HANA"}>HANA instance — sapcontrol of HDB&lt;NN&gt; (DB availability + start/stop)</option>
                <option value="node_exporter">Host metrics (Prometheus node_exporter)</option>
                <option value="os_ssh">OS inventory over SSH (root or &lt;sid&gt;adm, read-only)</option>
              </select></label>
            <label className="field"><span>Host</span><input required value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} placeholder={type === "hana" ? "hdb.corp.example.com" : "sapascs.corp.example.com"} />
              <small>Use "simulator" for demo data when enabled on the server.</small></label>
            <label className="field"><span>Port</span><input type="number" required value={form.port} onChange={(e) => setForm({ ...form, port: Number(e.target.value) })} />
              <small>{type === "hana" ? "3<NN>15 tenant / 3<NN>13 SYSTEMDB" : type === "node_exporter" ? "9100 (node_exporter)" : type === "os_ssh" ? "22 (SSH); the host key is pinned on first contact" : "5<NN>13 HTTP / 5<NN>14 HTTPS" + (type === "hana_sapcontrol" ? " of the HDB instance" : "")}</small></label>
            {type !== "node_exporter" && <><label className="field"><span>{type === "hana" ? "Database user" : type === "hana_sapcontrol" ? "OS user (<hanasid>adm)" : type === "os_ssh" ? "OS user (root or <sid>adm)" : "OS user (<sid>adm)"}</span><input value={form.username} autoComplete="off" onChange={(e) => setForm({ ...form, username: e.target.value })} placeholder={type === "hana" ? "MONITORING_RO" : "s4padm"} /></label>
            <label className="field"><span>Password</span><input type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /><small>Stored encrypted; never shown again.</small></label></>}
            {type === "node_exporter" && <p className="muted small">No credentials are sent. Restrict port 9100 to Sentinel or expose it through a trusted TLS reverse proxy.</p>}
            {type === "hana" && <label className="field"><span>ABAP schema (optional)</span><input value={form.abap_schema} onChange={(e) => setForm({ ...form, abap_schema: e.target.value.toUpperCase() })} placeholder="SAPHANADB" /><small>Enables dumps, RFC, IDoc, job and import monitors.</small></label>}
            <label className="field"><span>Interval (seconds)</span><input type="number" min={30} max={86400} value={form.interval_seconds} onChange={(e) => setForm({ ...form, interval_seconds: Number(e.target.value) })} /></label>
            <label className="field check"><input type="checkbox" checked={form.use_tls} onChange={(e) => setForm({ ...form, use_tls: e.target.checked })} /> <span>Use TLS</span></label>
            <label className="field check"><input type="checkbox" checked={form.verify_tls} disabled={!form.use_tls} onChange={(e) => setForm({ ...form, verify_tls: e.target.checked })} /> <span>Verify certificate</span></label>
          </div>
          <div className="actions" style={{ marginTop: 12 }}>
            <button type="button" className="btn" disabled={busy === "test" || !form.host} onClick={() => run("test", async () => {
              const r = await api.testCollector(system.id, body());
              return { ok: r.ok, text: r.ok ? `Connection OK (${r.duration_ms} ms)` : `Test failed: ${r.error}`,
                       details: r.diagnostics?.map((d) => `${d.ok ? "✓" : "✗"} ${d.instance ? `${d.instance} ` : ""}${d.call}${d.error ? `: ${d.error}` : ""}`) };
            })}>{busy === "test" ? "Testing…" : "Test connection"}</button>
            <button type="submit" className="btn primary" disabled={busy === "add"}>Add collector</button>
          </div>
        </form>
      </RequireRole>
      {!can("admin") && <p className="muted small">Administrators configure collectors; operators can run them.</p>}
    </Card>
  );
}
