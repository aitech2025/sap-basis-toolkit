import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { LandscapeDiagram } from "../components/LandscapeDiagram";
import { Card, DataTable, ErrorBanner, Pill, fmtDate, useAsync } from "../components/ui";
import type { RouteProposal, TransportRoute } from "../types";

export default function LandscapeDetail() {
  const landscape = decodeURIComponent(useParams().name ?? "");
  const { data, error, reload } = useAsync(() => api.topology(landscape), [landscape]);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [proposals, setProposals] = useState<RouteProposal[] | null>(null);
  const [form, setForm] = useState({ source: "", target: "", route_type: "consolidation", transport_layer: "" });

  const systems = data?.lanes.flatMap((l) => l.systems.map((s) => ({ ...s, lane: l.product_label }))) ?? [];
  const label = (id: number) => {
    const s = systems.find((x) => x.id === id);
    return s ? `${s.sid} (${s.environment})` : `#${id}`;
  };

  const act = async (fn: () => Promise<string>) => {
    setMessage(null);
    try {
      setMessage({ ok: true, text: await fn() });
      reload();
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{landscape} <span className="muted" style={{ fontWeight: 500 }}>landscape</span></h1>
          <p><Link to="/landscapes">All landscapes</Link> · click a system to open it, hover to trace its routes.</p>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => act(async () => {
            const r = await api.suggestRoutes(landscape);
            setProposals(r.proposals);
            return r.proposals.length ? `${r.proposals.length} route(s) suggested — review below` : "No new routes to suggest";
          })}>Suggest routes</button>
          <Link className="btn primary" to={`/systems?new=1`}>Add system</Link>
        </div>
      </div>
      <ErrorBanner error={error} />
      {message && <div className={`banner ${message.ok ? "ok" : "error"}`}>{message.text}</div>}
      {data?.warnings.map((w) => <div key={w} className="banner info">{w}</div>)}

      {proposals && proposals.length > 0 && (
        <Card title="Suggested routes" actions={<>
          <button className="btn" onClick={() => setProposals(null)}>Dismiss</button>
          <button className="btn primary" onClick={() => act(async () => {
            const r = await api.suggestRoutes(landscape, true);
            setProposals(null);
            return `Created ${r.created.length} route(s)`;
          })}>Create all</button>
        </>}>
          <DataTable<RouteProposal> rows={proposals} rowKey={(p) => `${p.source_system_id}-${p.target_system_id}`}
            columns={[
              { key: "path", label: "Route", render: (p) => <strong>{p.source_sid} → {p.target_sid}</strong> },
              { key: "route_type", label: "Type" },
              { key: "transport_layer", label: "Layer", render: (p) => p.transport_layer ?? "–" },
              { key: "mechanism", label: "Mechanism" },
            ]} />
          <p className="muted">Suggested where each product has exactly one system per tier (DEV → QAS → PRE → PRD).</p>
        </Card>
      )}

      {data && (
        <Card title="Transport path">
          <LandscapeDiagram topology={data} />
          <div className="legend">
            <span><svg width="34" height="10"><line x1="0" y1="5" x2="34" y2="5" className="legend-line" /></svg> route, no import checked yet</span>
            <span><svg width="34" height="10"><line x1="0" y1="5" x2="34" y2="5" className="legend-line ok" /></svg> last import OK</span>
            <span><svg width="34" height="10"><line x1="0" y1="5" x2="34" y2="5" className="legend-line review" /></svg> import warnings (RC 4)</span>
            <span><svg width="34" height="10"><line x1="0" y1="5" x2="34" y2="5" className="legend-line fail" /></svg> last import failed (RC ≥ 8)</span>
          </div>
        </Card>
      )}

      <Card title="Routes">
        <DataTable<TransportRoute> rows={data?.routes ?? []} rowKey={(r) => r.id} empty="No routes yet — add one below or use Suggest routes."
          columns={[
            { key: "path", label: "Route", render: (r) => <strong>{r.source_sid} → {r.target_sid}</strong> },
            { key: "route_type", label: "Type" },
            { key: "transport_layer", label: "Layer", render: (r) => r.transport_layer ?? "–" },
            { key: "mechanism", label: "Mechanism" },
            { key: "last_import", label: "Last import into target", render: (r) => r.last_import
              ? <><Pill value={r.last_import.status} /> <span className="muted">{fmtDate(r.last_import.at)}</span></>
              : <span className="muted">–</span> },
            { key: "del", label: "", render: (r) => (
              <button className="btn small danger" onClick={() => window.confirm(`Delete route ${r.source_sid} → ${r.target_sid}?`) &&
                act(async () => { await api.deleteRoute(r.id); return "Route deleted"; })}>Delete</button>) },
          ]} />
        <form className="row" style={{ marginTop: 12 }} onSubmit={(e) => {
          e.preventDefault();
          act(async () => {
            const r = await api.createRoute({ source_system_id: Number(form.source), target_system_id: Number(form.target),
              route_type: form.route_type, transport_layer: form.transport_layer || null });
            return `Route ${r.source_sid} → ${r.target_sid} created${r.warnings?.length ? ` (note: ${r.warnings.join("; ")})` : ""}`;
          });
        }}>
          <label className="field"><span>From</span>
            <select required value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}>
              <option value="">Source system</option>
              {systems.map((s) => <option key={s.id} value={s.id}>{s.sid} · {s.environment} · {s.lane}</option>)}
            </select></label>
          <label className="field"><span>To</span>
            <select required value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })}>
              <option value="">Target system</option>
              {systems.filter((s) => String(s.id) !== form.source).map((s) => <option key={s.id} value={s.id}>{s.sid} · {s.environment} · {s.lane}</option>)}
            </select></label>
          <label className="field"><span>Type</span>
            <select value={form.route_type} onChange={(e) => setForm({ ...form, route_type: e.target.value })}>
              <option value="consolidation">Consolidation (DEV → first target)</option>
              <option value="delivery">Delivery (onward)</option>
            </select></label>
          <label className="field"><span>Transport layer</span>
            <input value={form.transport_layer} maxLength={20} placeholder="ZS4D or SAP" onChange={(e) => setForm({ ...form, transport_layer: e.target.value })} /></label>
          <button className="btn primary" type="submit">Add route</button>
        </form>
        {form.source && form.target && <p className="muted">{label(Number(form.source))} → {label(Number(form.target))}</p>}
      </Card>
    </>
  );
}
