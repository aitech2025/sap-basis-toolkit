import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Card, DataTable, ErrorBanner, Pill, Tile, useAsync } from "../components/ui";
import type { DiscoveredInstance, RegisterResult, ScanResult } from "../types";

export const ROLES = ["ASCS", "SCS", "ERS", "PAS", "AAS", "JAVA", "HANA", "WEBDISP", "SMDA", "GATEWAY", "OTHER"];
const ROLE_SOURCE: Record<string, string> = {
  instance_name: "from instance name", features: "from features", inferred: "inferred", manual: "set manually",
};

export function statusPill(status: string) {
  const s = status.toUpperCase();
  return <Pill value={s === "GREEN" ? "running" : s === "YELLOW" ? "degraded" : s === "RED" ? "failed" : "stopped"} />;
}

interface Connect {
  host: string; instance_nr: string; use_tls: boolean; verify_tls: boolean; username: string; password: string;
}

export default function Discover() {
  const navigate = useNavigate();
  const catalogue = useAsync(() => api.catalogue(), []);
  const [conn, setConn] = useState<Connect>({ host: "", instance_nr: "", use_tls: false, verify_tls: true, username: "", password: "" });
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [busy, setBusy] = useState<"scan" | "register" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [target, setTarget] = useState<string>("new");
  const [hana, setHana] = useState({ enabled: false, host: "", port: "30015", username: "", password: "", database_name: "", abap_schema: "SAPHANADB" });
  const [result, setResult] = useState<RegisterResult | null>(null);
  const [dbCtl, setDbCtl] = useState({ enabled: true, username: "", password: "" });

  const doScan = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy("scan");
    setError(null);
    setScan(null);
    try {
      const found = await api.discoveryScan({
        host: conn.host.trim(), instance_nr: conn.instance_nr || null, use_tls: conn.use_tls, verify_tls: conn.verify_tls,
        username: conn.username || null, password: conn.password || null,
      });
      setScan(found);
      setRoles({});
      setTarget(found.existing_systems[0] ? String(found.existing_systems[0].id) : "new");
      setForm({
        name: `${found.sid} ${catalogue.data?.products.find((p) => p.key === found.product)?.label ?? found.product}`,
        environment: found.suggested_environment, landscape: found.existing_systems[0]?.landscape ?? "default",
        product: found.product, database: found.database ?? "", interval_seconds: "300", control_enabled: "",
      });
      setHana((h) => ({ ...h, enabled: false, host: found.db_host ?? "" }));
      setDbCtl({ enabled: !!found.hana_instance, username: found.hana_instance?.suggested_user ?? "", password: "" });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const register = async () => {
    if (!scan) return;
    setBusy("register");
    setError(null);
    try {
      const res = await api.discoveryRegister({
        host: conn.host.trim(), instance_nr: conn.instance_nr || null, use_tls: conn.use_tls, verify_tls: conn.verify_tls,
        username: conn.username || null, password: conn.password || null, expected_sid: scan.sid,
        system_id: target === "new" ? null : Number(target),
        name: form.name || null, environment: form.environment, landscape: form.landscape || "default",
        product: form.product, database: form.database || null, interval_seconds: Number(form.interval_seconds) || 300,
        control_enabled: form.control_enabled === "" ? null : form.control_enabled === "on",
        role_overrides: roles,
        db_control: dbCtl.enabled && scan.hana_instance ? { username: dbCtl.username, password: dbCtl.password } : null,
        hana: hana.enabled ? { host: hana.host, port: Number(hana.port) || 30015, username: hana.username,
          password: hana.password, database_name: hana.database_name || null, abap_schema: hana.abap_schema || null } : null,
      });
      setResult(res);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const key = (i: DiscoveredInstance) => `${i.hostname}:${i.instance_nr}`;
  const product = catalogue.data?.products.find((p) => p.key === form.product);
  const dbChoices = catalogue.data?.databases.filter((d) => !product?.databases || product.databases.includes(d.key)) ?? [];

  if (result) {
    return (
      <>
        <div className="page-head"><div><h1>{result.system.sid} {result.created ? "registered" : "updated"}</h1>
          <p>Monitoring is running. Instances are kept in sync on every collection.</p></div></div>
        <Card title="First collection">
          <ul className="plain">
            {result.first_runs.map((r) => {
              const c = result.collectors.find((x) => x.id === r.collector_id);
              return <li key={r.collector_id}><Pill value={r.status} /> {c?.type} collector on {c?.host}:{c?.port}
                {r.error && <span className="muted"> — {r.error}</span>}</li>;
            })}
            {!result.first_runs.length && <li className="muted">Monitoring not started.</li>}
          </ul>
          <p className="muted">{result.instances.length} instances in the inventory.</p>
          <div className="actions">
            <button className="btn primary" onClick={() => navigate(`/systems/${result.system.id}`)}>Open {result.system.sid}</button>
            <button className="btn" onClick={() => navigate(`/systems/${result.system.id}/instances`)}>Instances &amp; control</button>
            <button className="btn" onClick={() => { setResult(null); setScan(null); setConn({ ...conn, host: "", password: "" }); }}>
              Discover another</button>
          </div>
        </Card>
      </>
    );
  }

  return (
    <>
      <div className="page-head">
        <div><h1>Discover SAP system</h1>
          <p>Enter a host and the &lt;sid&gt;adm credentials. Sentinel finds the SAP Start Service, reads the SID, every
            instance (ASCS/SCS, ERS, PAS, AAS, Java, Web Dispatcher), the product and database, then registers it and starts monitoring.</p></div>
      </div>
      <ErrorBanner error={error || catalogue.error} />
      <Card title="1 · Connect">
        <form onSubmit={doScan}>
          <div className="form-grid">
            <label className="field"><span>Hostname or IP</span>
              <input required value={conn.host} onChange={(e) => setConn({ ...conn, host: e.target.value })}
                     placeholder="s4hprd.corp.example.com" autoComplete="off" />
              <small>Must be allowed by ALLOWED_TARGET_HOSTS / CIDRS. Demo: simulator-s4p, simulator-ecd, simulator-epq.</small></label>
            <label className="field"><span>Instance number (optional)</span>
              <input value={conn.instance_nr} maxLength={2} inputMode="numeric" placeholder="scan 00–99"
                     onChange={(e) => setConn({ ...conn, instance_nr: e.target.value.replace(/\D/g, "") })} /></label>
            <label className="field"><span>OS user</span>
              <input value={conn.username} onChange={(e) => setConn({ ...conn, username: e.target.value })} placeholder="s4hadm" autoComplete="off" />
              <small>&lt;sid&gt;adm or a user in service/admin_users. Needed for protected methods and start/stop.</small></label>
            <label className="field"><span>Password</span>
              <input type="password" value={conn.password} onChange={(e) => setConn({ ...conn, password: e.target.value })} autoComplete="new-password" /></label>
            <label className="field check"><input type="checkbox" checked={conn.use_tls} onChange={(e) => setConn({ ...conn, use_tls: e.target.checked })} />
              <span>HTTPS (port 5&lt;NN&gt;14)</span></label>
            {conn.use_tls && <label className="field check"><input type="checkbox" checked={conn.verify_tls}
              onChange={(e) => setConn({ ...conn, verify_tls: e.target.checked })} /><span>Verify certificate</span></label>}
          </div>
          <div className="actions" style={{ marginTop: 12 }}>
            <button className="btn primary" type="submit" disabled={busy !== null}>{busy === "scan" ? "Detecting…" : "Detect system"}</button>
          </div>
        </form>
      </Card>

      {scan && (
        <>
          <Card title={`2 · Review ${scan.sid}`}>
            <div className="tiles">
              <Tile label="SID" value={scan.sid} hint={scan.host} />
              <Tile label="Product" value={catalogue.data?.products.find((p) => p.key === scan.product)?.label ?? scan.product}
                    hint={[scan.product_version, scan.product_evidence].filter(Boolean).join(" · ") || "not detected"} />
              <Tile label="Stack · DB" value={`${scan.stack} · ${scan.database ?? "?"}`} hint={scan.db_host ? `DB host ${scan.db_host}` : undefined} />
              <Tile label="SAP_BASIS" value={scan.basis_release ?? "–"} />
              <Tile label="Instances" value={scan.instances.length}
                    hint={`${scan.instances.filter((i) => i.status === "GREEN").length} running`} />
              <Tile label="Start/stop access" value={scan.control_access === null ? "unknown" : scan.control_access ? "yes" : "no"} />
            </div>
            {scan.warnings.length > 0 && (
              <div className="banner warn" role="status"><ul className="plain">{scan.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>
            )}
            <h3 style={{ marginTop: 16 }}>Instances</h3>
            <DataTable<DiscoveredInstance>
              rows={scan.instances} rowKey={key}
              columns={[
                { key: "hostname", label: "Host" },
                { key: "instance_nr", label: "Nr" },
                { key: "instance_name", label: "Instance", render: (i) => i.instance_name ?? "–" },
                { key: "role", label: "Role", render: (i) => (
                  <select aria-label={`Role of ${i.hostname} ${i.instance_nr}`} value={roles[key(i)] ?? i.role}
                          onChange={(e) => setRoles((r) => {
                            const next = { ...r };
                            if (e.target.value === i.role) delete next[key(i)]; else next[key(i)] = e.target.value;
                            return next;
                          })}>
                    {ROLES.map((r) => <option key={r}>{r}</option>)}
                  </select>) },
                { key: "source", label: "Detected", render: (i) => <span className="muted">{roles[key(i)] ? "set manually" : ROLE_SOURCE[i.role_source]}</span> },
                { key: "features", label: "Features", render: (i) => <span className="muted small">{i.features}</span> },
                { key: "status", label: "Status", render: (i) => statusPill(i.status) },
              ]}
            />
          </Card>
          {scan.hana_instance && (
            <Card title={`HANA database ${scan.hana_instance.sid} · HDB${scan.hana_instance.instance_nr} on ${scan.hana_instance.host}`}>
              <p className="muted small">Found the HANA instance's own SAP Start Service (port {scan.hana_instance.port}). With its OS user,
                Sentinel monitors database availability and includes HANA in the start/stop procedures (database first on start, last on stop).</p>
              <div className="form-grid">
                <label className="field check wide"><input type="checkbox" checked={dbCtl.enabled} onChange={(e) => setDbCtl({ ...dbCtl, enabled: e.target.checked })} />
                  <span>Monitor and control the HANA instance</span></label>
                {dbCtl.enabled && (
                  <>
                    <label className="field"><span>OS user (&lt;hanasid&gt;adm)</span>
                      <input value={dbCtl.username} onChange={(e) => setDbCtl({ ...dbCtl, username: e.target.value })} autoComplete="off" /></label>
                    <label className="field"><span>Password</span>
                      <input type="password" value={dbCtl.password} onChange={(e) => setDbCtl({ ...dbCtl, password: e.target.value })} autoComplete="new-password" /></label>
                  </>
                )}
              </div>
            </Card>
          )}
          <Card title="3 · Register and monitor">
            <div className="form-grid">
              {scan.existing_systems.length > 0 && (
                <label className="field wide"><span>Register as</span>
                  <select value={target} onChange={(e) => setTarget(e.target.value)}>
                    {scan.existing_systems.map((s) => <option key={s.id} value={s.id}>Update existing {s.sid} in {s.landscape} ({s.name})</option>)}
                    <option value="new">New system</option>
                  </select></label>
              )}
              <label className="field"><span>Name</span><input value={form.name ?? ""} onChange={(e) => set("name", e.target.value)} /></label>
              <label className="field"><span>Environment</span>
                <select value={form.environment} onChange={(e) => set("environment", e.target.value)}>
                  {catalogue.data?.environments.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
                </select><small>Suggested from the SID's last letter.</small></label>
              <label className="field"><span>Landscape</span><input value={form.landscape ?? ""} onChange={(e) => set("landscape", e.target.value)} /></label>
              <label className="field"><span>Product</span>
                <select value={form.product} onChange={(e) => set("product", e.target.value)}>
                  {catalogue.data?.products.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                </select></label>
              <label className="field"><span>Database</span>
                <select value={form.database} onChange={(e) => set("database", e.target.value)} required>
                  <option value="">Choose…</option>
                  {dbChoices.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
                </select></label>
              <label className="field"><span>Collect every</span>
                <select value={form.interval_seconds} onChange={(e) => set("interval_seconds", e.target.value)}>
                  {[60, 300, 600, 900].map((s) => <option key={s} value={s}>{s / 60} min</option>)}
                </select></label>
              <label className="field"><span>Start / stop from Sentinel</span>
                <select value={form.control_enabled} onChange={(e) => set("control_enabled", e.target.value)}>
                  <option value="">Default (off for PRD)</option><option value="on">Allowed</option><option value="off">Not allowed</option>
                </select></label>
              <label className="field check wide"><input type="checkbox" checked={hana.enabled} onChange={(e) => setHana({ ...hana, enabled: e.target.checked })} />
                <span>Also monitor the HANA database (SQL user with MONITORING role)</span></label>
              {hana.enabled && (
                <>
                  <label className="field"><span>HANA host</span><input value={hana.host} onChange={(e) => setHana({ ...hana, host: e.target.value })} /></label>
                  <label className="field"><span>SQL port</span><input value={hana.port} onChange={(e) => setHana({ ...hana, port: e.target.value })} />
                    <small>3&lt;NN&gt;15 for the first tenant, 3&lt;NN&gt;13 for SYSTEMDB.</small></label>
                  <label className="field"><span>DB user</span><input value={hana.username} onChange={(e) => setHana({ ...hana, username: e.target.value })} autoComplete="off" /></label>
                  <label className="field"><span>Password</span><input type="password" value={hana.password} onChange={(e) => setHana({ ...hana, password: e.target.value })} autoComplete="new-password" /></label>
                  <label className="field"><span>Tenant database</span><input value={hana.database_name} onChange={(e) => setHana({ ...hana, database_name: e.target.value })} placeholder={scan.sid} /></label>
                  <label className="field"><span>ABAP schema</span><input value={hana.abap_schema} onChange={(e) => setHana({ ...hana, abap_schema: e.target.value.toUpperCase() })} placeholder="SAPHANADB" /></label>
                </>
              )}
            </div>
            <p className="muted small">The host is scanned again on registration. Passwords are stored encrypted and never shown again.</p>
            <div className="actions">
              <button className="btn primary" onClick={register} disabled={busy !== null || !form.database}>
                {busy === "register" ? "Registering…" : target === "new" ? `Register ${scan.sid} and start monitoring` : `Update ${scan.sid}`}</button>
              <Link className="btn" to="/systems">Cancel</Link>
            </div>
          </Card>
        </>
      )}
    </>
  );
}
