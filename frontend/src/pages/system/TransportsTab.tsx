import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { Card, DataTable, Empty, ErrorBanner, FileField, Pill, useAsync } from "../../components/ui";
import type { PostcheckResult, PrecheckResult, SystemDetail } from "../../types";

const list = (v: string) => v.split(/[\s,;]+/).map((x) => x.trim().toUpperCase()).filter(Boolean);

export default function TransportsTab({ system }: { system: SystemDetail }) {
  const cap = system.capabilities.transports;
  const [e071, setE071] = useState<string | null>(null);
  const [e070, setE070] = useState<string | null>(null);
  const [queue, setQueue] = useState("");
  const [imported, setImported] = useState("");
  const [approved, setApproved] = useState("");
  const [log, setLog] = useState<string | null>(null);
  const [pre, setPre] = useState<PrecheckResult | null>(null);
  const [post, setPost] = useState<PostcheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const routes = useAsync(() => api.systemRoutes(system.id), [system.id]);

  if (!cap.supported) {
    return <Card title="Transports"><Empty>{cap.how_to_export}</Empty></Card>;
  }

  const guard = async (fn: () => Promise<void>) => {
    setError(null);
    try { await fn(); } catch (e) { setError((e as Error).message); }
  };

  return (
    <>
      <ErrorBanner error={error} />
      <Card title="Transport path" actions={<Link className="btn small" to={`/landscapes/${encodeURIComponent(system.landscape)}`}>Landscape diagram</Link>}>
        {routes.data?.hops.length ? (
          <div className="route-path" aria-label="Transport path">
            {routes.data.hops.map((layer, i) => (
              <Fragment key={layer.map((h) => h.system_id).join("-")}>
                {i > 0 && <span className="muted" aria-hidden="true">→</span>}
                <span className="hop-group">
                  {layer.map((hop) => hop.system_id === system.id
                    ? <span key={hop.system_id} className="hop current" aria-current="true">{hop.sid}</span>
                    : <Link key={hop.system_id} className="hop" to={`/systems/${hop.system_id}/transports`}>{hop.sid}</Link>)}
                </span>
              </Fragment>
            ))}
          </div>
        ) : <p className="muted" style={{ margin: 0 }}>No transport routes for {system.sid} yet. Define them in the landscape diagram.</p>}
        {!!routes.data?.inbound.length && (
          <p className="muted">Imports arrive from {routes.data.inbound.map((r) => `${r.source_sid} (${r.route_type}${r.transport_layer ? `, layer ${r.transport_layer}` : ""})`).join(", ")}.
            Use the E070/E071 exports of the source system for the pre-check.</p>
        )}
      </Card>
      <Card title={`Pre-import check — target ${system.sid}`}>
        <form onSubmit={(e) => { e.preventDefault(); guard(async () => {
          if (!e071) throw new Error("Choose an E071 export from the source system");
          const body: Record<string, unknown> = { e071_csv: e071, e070_csv: e070, approved_tables: list(approved) };
          if (queue.trim()) body.queue = list(queue);
          if (imported.trim()) body.imported_in_target = list(imported);
          setPre(await api.precheck(system.id, body));
        }); }}>
          <div className="form-grid">
            <FileField label="E071 export (source system)" accept=".csv,.txt" onText={(c) => setE071(c as string | null)} />
            <FileField label="E070 export (optional)" accept=".csv,.txt" onText={(c) => setE070(c as string | null)} />
            <label className="field"><span>Approved protected tables</span><input value={approved} onChange={(e) => setApproved(e.target.value)} placeholder="TVARVC, ZCONFIG" /></label>
            <label className="field wide"><span>Import queue in order (optional)</span><textarea rows={2} value={queue} onChange={(e) => setQueue(e.target.value)} placeholder="DEVK900100 DEVK900110" /><small>Defaults to all requests ordered by release time.</small></label>
            <label className="field wide"><span>Already imported into {system.sid} (optional)</span><textarea rows={2} value={imported} onChange={(e) => setImported(e.target.value)} /><small>Paste from STMS import history to enable the missing-predecessor check.</small></label>
          </div>
          <button className="btn primary" style={{ marginTop: 12 }} type="submit">Run pre-check</button>
        </form>
      </Card>
      {pre && (
        <Card title={<>Verdict <Pill value={pre.verdict} /></>}>
          {pre.notes.map((n) => <p key={n} className="muted">{n}</p>)}
          <DataTable rows={pre.findings} rowKey={(f, i) => `${f.code}-${f.request}-${i}`} empty="No findings — safe to import."
            columns={[
              { key: "severity", label: "Severity", render: (f) => <Pill value={f.severity} /> },
              { key: "code", label: "Check", render: (f) => <code>{f.code}</code> },
              { key: "request", label: "Request" },
              { key: "message", label: "Detail" },
            ]} />
        </Card>
      )}
      <Card title="Post-import check (alerts on RC ≥ 8)">
        <form className="row" onSubmit={(e) => { e.preventDefault(); guard(async () => {
          if (!log) throw new Error("Choose an import history export or tp log");
          setPost(await api.postcheck(system.id, log));
        }); }}>
          <FileField label="STMS import history CSV or tp/ALOG text" onText={(c) => setLog(c as string | null)} />
          <button className="btn primary" type="submit">Run post-check</button>
        </form>
        {post && (
          <>
            <p>{post.requests} requests · <strong>{post.failed} failed</strong> · {post.warnings} warnings · {post.alerts.filter((a) => a.result === "sent").length} alerts sent</p>
            <DataTable rows={post.results} rowKey={(r) => r.request}
              columns={[{ key: "request", label: "Request" }, { key: "rc", label: "RC", numeric: true },
                        { key: "result", label: "Result", render: (r) => <Pill value={r.result} /> }]} />
          </>
        )}
        <details style={{ marginTop: 12 }}><summary className="muted">How to export</summary><pre>{cap.how_to_export}</pre></details>
      </Card>
    </>
  );
}
