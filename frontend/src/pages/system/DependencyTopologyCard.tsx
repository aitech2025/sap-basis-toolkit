import { useMemo, useState } from "react";
import { api } from "../../api";
import { Card, Empty, ErrorBanner, useAsync } from "../../components/ui";
import { RequireRole } from "../../components/session";
import type { SystemDetail } from "../../types";

const KINDS = ["host", "hana", "rfc_destination", "middleware", "web_dispatcher", "external_service"];
const RELATIONSHIPS = ["runs_on", "uses_database", "connects_to", "routes_to", "depends_on", "hosts"];

export default function DependencyTopologyCard({ system }: { system: SystemDetail }) {
  const { data, error, loading, reload } = useAsync(() => api.dependencyTopology(system.id), [system.id]);
  const [kind, setKind] = useState("host");
  const [name, setName] = useState("");
  const [hostname, setHostname] = useState("");
  const [port, setPort] = useState("");
  const [shared, setShared] = useState(false);
  const [sourceId, setSourceId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [relationship, setRelationship] = useState("depends_on");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const assets = data?.assets ?? [];
  const byId = useMemo(() => new Map(assets.map((asset) => [asset.id, asset])), [assets]);

  const addAsset = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true); setActionError(null);
    try {
      await api.createTopologyAsset({
        landscape: system.landscape, system_id: shared ? null : system.id, kind, name,
        hostname: hostname || null, port: port ? Number(port) : null,
      });
      setName(""); setHostname(""); setPort(""); reload();
    } catch (e) { setActionError((e as Error).message); }
    finally { setBusy(false); }
  };

  const addLink = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true); setActionError(null);
    try {
      await api.createTopologyLink({
        source_asset_id: Number(sourceId), target_asset_id: Number(targetId), relationship,
      });
      reload();
    } catch (e) { setActionError((e as Error).message); }
    finally { setBusy(false); }
  };

  const removeAsset = async (id: number) => {
    if (!window.confirm("Remove this asset and its dependency links?")) return;
    setActionError(null);
    try { await api.deleteTopologyAsset(id); reload(); }
    catch (e) { setActionError((e as Error).message); }
  };

  const removeLink = async (id: number) => {
    setActionError(null);
    try { await api.deleteTopologyLink(id); reload(); }
    catch (e) { setActionError((e as Error).message); }
  };

  return (
    <Card title="Infrastructure dependency inventory">
      <p className="muted">Record confirmed relationships between this SAP system and its hosts, HANA database, RFC destinations, web dispatchers and middleware. Only explicit links are used; Sentinel does not guess dependencies.</p>
      <ErrorBanner error={error || actionError} />
      {loading && !data ? <p className="muted">Loading dependency inventory…</p> : (
        <>
          <div className="cols">
            <div>
              <h3>Assets ({assets.length})</h3>
              {assets.length ? <div className="table-wrap"><table>
                <thead><tr><th>Asset</th><th>Endpoint</th><th>Scope</th><th /></tr></thead>
                <tbody>{assets.map((asset) => <tr key={asset.id}>
                  <td><strong>{asset.name}</strong><div className="muted small">{asset.kind}</div></td>
                  <td>{asset.hostname ?? "–"}{asset.port ? `:${asset.port}` : ""}</td>
                  <td>{asset.system_id == null ? "Shared" : system.sid}</td>
                  <td><RequireRole role="admin"><button className="btn small danger" onClick={() => removeAsset(asset.id)}>Remove</button></RequireRole></td>
                </tr>)}</tbody>
              </table></div> : <Empty>No infrastructure assets recorded for this landscape.</Empty>}
            </div>
            <div>
              <h3>Confirmed dependencies ({data?.links.length ?? 0})</h3>
              {data?.links.length ? <div className="table-wrap"><table>
                <thead><tr><th>Source</th><th>Relationship</th><th>Target</th><th /></tr></thead>
                <tbody>{data.links.map((link) => <tr key={link.id}>
                  <td>{byId.get(link.source_asset_id)?.name ?? `Asset #${link.source_asset_id}`}</td>
                  <td><code>{link.relationship}</code></td>
                  <td>{byId.get(link.target_asset_id)?.name ?? `Asset #${link.target_asset_id}`}</td>
                  <td><RequireRole role="admin"><button className="btn small danger" onClick={() => removeLink(link.id)}>Remove</button></RequireRole></td>
                </tr>)}</tbody>
              </table></div> : <Empty>No dependency links yet. Add only relationships confirmed by your Basis or infrastructure team.</Empty>}
            </div>
          </div>
          <RequireRole role="admin">
            <div className="cols" style={{ marginTop: 18 }}>
              <form className="card-subsection" onSubmit={addAsset}>
                <h3>Add infrastructure asset</h3>
                <label className="field"><span>Asset type</span><select value={kind} onChange={(e) => setKind(e.target.value)}>{KINDS.map((v) => <option key={v}>{v}</option>)}</select></label>
                <label className="field"><span>Name</span><input required maxLength={160} value={name} onChange={(e) => setName(e.target.value)} placeholder="PRD application host" /></label>
                <label className="field"><span>Hostname (optional)</span><input maxLength={253} value={hostname} onChange={(e) => setHostname(e.target.value)} placeholder="sapapp.example.internal" /></label>
                <label className="field"><span>Port (optional)</span><input type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} /></label>
                <label className="field check"><input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} /><span>Shared infrastructure (not assigned to this SAP system)</span></label>
                <button className="btn primary" disabled={busy || !name.trim()}>Add asset</button>
              </form>
              <form className="card-subsection" onSubmit={addLink}>
                <h3>Add dependency link</h3>
                <label className="field"><span>Source asset</span><select required value={sourceId} onChange={(e) => setSourceId(e.target.value)}><option value="">Select source…</option>{assets.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.kind})</option>)}</select></label>
                <label className="field"><span>Relationship</span><select value={relationship} onChange={(e) => setRelationship(e.target.value)}>{RELATIONSHIPS.map((v) => <option key={v}>{v}</option>)}</select></label>
                <label className="field"><span>Target asset</span><select required value={targetId} onChange={(e) => setTargetId(e.target.value)}><option value="">Select target…</option>{assets.map((a) => <option key={a.id} value={a.id} disabled={String(a.id) === sourceId}>{a.name} ({a.kind})</option>)}</select></label>
                <button className="btn primary" disabled={busy || !sourceId || !targetId || sourceId === targetId}>Add dependency</button>
              </form>
            </div>
          </RequireRole>
        </>
      )}
    </Card>
  );
}
