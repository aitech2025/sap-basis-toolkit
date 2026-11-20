import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Card, DataTable, ErrorBanner, Pill, Tile, useAsync } from "../components/ui";
import { DatabaseCard, HostCard, LayerStack } from "../components/layers";
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
  const [method, setMethod] = useState<"os" | "sapcontrol">("os");
  const [os, setOs] = useState({ os_user: "root", os_password: "", use_key: false, os_private_key: "", ssh_port: "22",
    sap_password: "", db_show: false, db_os_user: "", db_os_password: "", sid: "" });
  const osPayload = (sid?: string) => ({
    method: "os", host: conn.host.trim(), os_user: os.os_user.trim(), ssh_port: Number(os.ssh_port) || 22,
    os_password: os.use_key ? null : os.os_password || null, os_private_key: os.use_key ? os.os_private_key || null : null,
    sid: (sid ?? os.sid) || null, use_tls: conn.use_tls, verify_tls: conn.verify_tls,
    db_os_user: os.db_show ? os.db_os_user || null : null, db_os_password: os.db_show ? os.db_os_password || null : null,
  });

  const doScan = async (e?: React.FormEvent, sid?: string) => {
    e?.preventDefault();
    setBusy("scan");
    setError(null);
    setScan(null);
    if (sid !== undefined) setOs((o) => ({ ...o, sid }));
    try {
      const found = await api.discoveryScan(method === "os" ? osPayload(sid) : {
        host: conn.host.trim(), instance_nr: conn.instance_nr || null, use_tls: conn.use_tls, verify_tls: conn.verify_tls,
        username: conn.username || null, password: conn.password || null,
      });
      setScan(found);
      setRoles({});
      setTarget(found.existing_systems[0] ? String(found.existing_systems[0].id) : "new");
      setForm({
        name: `${found.sid} ${catalogue.data?.products.find((p) => p.key === found.product)?.label ?? found.product}`,
        environment: found.suggested_environment, landscape: found.existing_systems[0]?.landscape ?? "default",
        product: found.product, database: found.database ?? "", interval_seconds: "", control_enabled: "",
      });
      setHana((h) => ({ ...h, enabled: false, host: found.database_layer?.host ?? found.db_host ?? "",
        abap_schema: found.database_layer?.schema ?? h.abap_schema, database_name: found.database_layer?.tenant ?? "" }));
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
        ...(method === "os" ? {
          ...osPayload(scan.sid), expected_host_key: scan.host_key, expected_db_host_key: scan.db_host_key ?? null,
          sap_user: !scan.sap_credentials && os.sap_password ? `${scan.sid.toLowerCase()}adm` : null,
          sap_password: !scan.sap_credentials && os.sap_password ? os.sap_password : null,
        } : {
          host: conn.host.trim(), instance_nr: conn.instance_nr || null, use_tls: conn.use_tls, verify_tls: conn.verify_tls,
          username: conn.username || null, password: conn.password || null,
        }),
        expected_sid: scan.sid,
        system_id: target === "new" ? null : Number(target),
        name: form.name || null, environment: form.environment, landscape: form.landscape || "default",
        product: form.product, database: form.database || null, interval_seconds: Number(form.interval_seconds) || null,
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
          <p>Enter one application server and an OS login. Sentinel detects the SID, every instance, the database server and the
            operating system of each host, registers all three layers and starts monitoring.</p></div>
      </div>
      <ErrorBanner error={error || catalogue.error} />
      <Card title="1 · Connect" description={method === "os"
        ? "Log in to an application server (ASCS or PAS host) as root or <sid>adm. Sentinel reads the SAP profiles and the OS over SSH with fixed, read-only commands, then follows SAPDBHOST to the database server."
        : "Connect to the SAP Start Service directly (no OS access). The OS layer is not inventoried in this mode."}>
        <div className="mb-5 inline-flex rounded-full bg-surface p-1" role="radiogroup" aria-label="Discovery method">
          {([["os", "OS login (root or <sid>adm)"], ["sapcontrol", "SAP Start Service only"]] as const).map(([m, label]) => (
            <button key={m} type="button" role="radio" aria-checked={method === m} onClick={() => { setMethod(m); setScan(null); }}
              className={`rounded-full px-4 py-2 text-sm font-semibold transition-colors ${method === m ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground"}`}>
              {label}
            </button>
          ))}
        </div>
        <form onSubmit={(e) => doScan(e)}>
          <div className="form-grid">
            <label className="field"><span>Application server host or IP</span>
              <input required value={conn.host} onChange={(e) => setConn({ ...conn, host: e.target.value })}
                     placeholder="s4hascs.corp.example.com" autoComplete="off" />
              <small>Must be allowed by ALLOWED_TARGET_HOSTS / CIDRS. Demo: simulator-s4p, simulator-ecd, simulator-epq.</small></label>
            {method === "os" ? (
              <>
                <label className="field"><span>OS user</span>
                  <input required value={os.os_user} onChange={(e) => setOs({ ...os, os_user: e.target.value })} placeholder="root or s4hadm" autoComplete="off" />
                  <small>root reads everything; &lt;sid&gt;adm also unlocks start/stop.</small></label>
                {os.use_key ? (
                  <label className="field wide"><span>Private key (OpenSSH or PEM)</span>
                    <textarea rows={4} className="font-mono text-xs" value={os.os_private_key} onChange={(e) => setOs({ ...os, os_private_key: e.target.value })}
                              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" spellCheck={false} /></label>
                ) : (
                  <label className="field"><span>Password</span>
                    <input type="password" required value={os.os_password} onChange={(e) => setOs({ ...os, os_password: e.target.value })} autoComplete="new-password" /></label>
                )}
                <label className="field"><span>SSH port</span>
                  <input inputMode="numeric" value={os.ssh_port} onChange={(e) => setOs({ ...os, ssh_port: e.target.value.replace(/\D/g, "") })} /></label>
                <label className="field check"><input type="checkbox" checked={os.use_key} onChange={(e) => setOs({ ...os, use_key: e.target.checked })} />
                  <span>Use a private key instead of a password</span></label>
                <label className="field check"><input type="checkbox" checked={os.db_show} onChange={(e) => setOs({ ...os, db_show: e.target.checked })} />
                  <span>The database server uses a different login</span></label>
                {os.db_show && (
                  <>
                    <label className="field"><span>Database server OS user</span>
                      <input value={os.db_os_user} onChange={(e) => setOs({ ...os, db_os_user: e.target.value })} placeholder="root or <hanasid>adm" autoComplete="off" /></label>
                    <label className="field"><span>Database server password</span>
                      <input type="password" value={os.db_os_password} onChange={(e) => setOs({ ...os, db_os_password: e.target.value })} autoComplete="new-password" /></label>
                  </>
                )}
              </>
            ) : (
              <>
                <label className="field"><span>Instance number (optional)</span>
                  <input value={conn.instance_nr} maxLength={2} inputMode="numeric" placeholder="scan 00–99"
                         onChange={(e) => setConn({ ...conn, instance_nr: e.target.value.replace(/\D/g, "") })} /></label>
                <label className="field"><span>OS user</span>
                  <input value={conn.username} onChange={(e) => setConn({ ...conn, username: e.target.value })} placeholder="s4hadm" autoComplete="off" />
                  <small>&lt;sid&gt;adm or a user in service/admin_users. Needed for protected methods and start/stop.</small></label>
                <label className="field"><span>Password</span>
                  <input type="password" value={conn.password} onChange={(e) => setConn({ ...conn, password: e.target.value })} autoComplete="new-password" /></label>
              </>
            )}
            <label className="field check"><input type="checkbox" checked={conn.use_tls} onChange={(e) => setConn({ ...conn, use_tls: e.target.checked })} />
              <span>SAP Start Service over HTTPS (5&lt;NN&gt;14)</span></label>
            {conn.use_tls && <label className="field check"><input type="checkbox" checked={conn.verify_tls}
              onChange={(e) => setConn({ ...conn, verify_tls: e.target.checked })} /><span>Verify certificate</span></label>}
          </div>
          <div className="actions" style={{ marginTop: 16 }}>
            <button className="btn primary" type="submit" disabled={busy !== null}>{busy === "scan" ? "Detecting…" : "Detect system"}</button>
          </div>
        </form>
      </Card>

      {scan?.method === "os" && scan.os && (
        <Card title={`Detected layers of ${scan.sid}`}
              description="Read from /usr/sap/sapservices, the SAP profiles (DEFAULT.PFL) and the operating system of each server."
              actions={(scan.sids_on_host ?? []).filter((x) => x.kind === "application").length > 1 && (
                <select aria-label="SAP system on this host" className="filter-select !w-auto" value={scan.sid} onChange={(e) => doScan(undefined, e.target.value)}>
                  {(scan.sids_on_host ?? []).filter((x) => x.kind === "application").map((x) => <option key={x.sid} value={x.sid}>{x.sid}</option>)}
                </select>
              )}>
          <LayerStack
            application={
              <div className="rounded-2xl bg-surface p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-lg font-bold">{scan.sid}</span>
                  <span className="rounded-full bg-accent px-2.5 py-0.5 text-xs font-semibold text-accent-foreground">
                    {catalogue.data?.products.find((p) => p.key === scan.product)?.label ?? scan.product}{scan.product_version ? ` ${scan.product_version}` : ""}</span>
                  <span className="text-xs text-muted-foreground">{scan.stack}, {scan.instances.length} instances</span>
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {scan.instances.map((i) => <span key={key(i)} className="rounded-full bg-card px-2.5 py-1 font-mono text-xs"><strong>{i.role}</strong> {i.instance_name ?? i.instance_nr} · {i.hostname}</span>)}
                </div>
                {(scan.sids_on_host ?? []).length > 1 && (
                  <p className="mt-3 text-xs text-muted-foreground">Also on this host: {(scan.sids_on_host ?? []).filter((x) => x.sid !== scan.sid)
                    .map((x) => `${x.sid} (${x.kind === "agent" ? "diagnostics agent" : x.kind})`).join(", ")}</p>
                )}
              </div>
            }
            database={<DatabaseCard db={scan.database_layer} fallbackType={scan.database ?? undefined} />}
            hosts={
              <div className="grid gap-3 xl:grid-cols-2">
                <HostCard host={scan.host} os={scan.os} hostKey={scan.host_key} role={scan.database_layer?.on_application_host ? "application + database" : "application"} />
                {scan.database_layer && !scan.database_layer.on_application_host && (
                  <HostCard host={scan.database_layer.host} os={scan.db_os} hostKey={scan.db_host_key} role="database" />
                )}
              </div>
            }
          />
          <p className="text-xs text-muted-foreground">SSH host keys are pinned when you register. Sentinel refuses to connect later if a key changes.</p>
        </Card>
      )}

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
              {scan.method === "os" && !scan.sap_credentials && (
                <label className="field wide"><span>{scan.sid.toLowerCase()}adm password (optional)</span>
                  <input type="password" value={os.sap_password} onChange={(e) => setOs({ ...os, sap_password: e.target.value })} autoComplete="new-password" />
                  <small>You logged in as {scan.os_user}. The SAP Start Service needs {scan.sid.toLowerCase()}adm for protected monitoring and start/stop.</small></label>
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
                  <option value="">Recommended (SAP 1 min, database 5 min, OS 15 min)</option>
                  {[60, 300, 600, 900].map((s) => <option key={s} value={s}>Everything every {s / 60} min</option>)}
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
