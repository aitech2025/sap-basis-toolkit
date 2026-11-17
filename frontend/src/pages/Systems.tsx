import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { SystemForm } from "../components/SystemForm";
import { Card, DataTable, ErrorBanner, Pill, useAsync } from "../components/ui";
import type { SapSystem } from "../types";

export default function Systems() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { data, error, reload } = useAsync(() => api.systems(), []);
  const catalogue = useAsync(() => api.catalogue(), []);
  const [filter, setFilter] = useState({ text: "", product: "", environment: "", landscape: "" });
  const open = params.get("new") === "1";

  const landscapes = useMemo(() => [...new Set((data ?? []).map((s) => s.landscape))].sort(), [data]);
  const rows = (data ?? []).filter((s) =>
    (!filter.text || `${s.sid} ${s.name} ${s.host ?? ""}`.toLowerCase().includes(filter.text.toLowerCase())) &&
    (!filter.product || s.product === filter.product) &&
    (!filter.environment || s.environment === filter.environment) &&
    (!filter.landscape || s.landscape === filter.landscape));

  return (
    <>
      <div className="page-head">
        <div><h1>Systems</h1><p>Register ECC, S/4HANA, BW, NetWeaver ABAP/Java and other SAP systems.</p></div>
        <button className="btn primary" onClick={() => setParams({ new: "1" })}>Add system</button>
      </div>
      <ErrorBanner error={error || catalogue.error} />
      <Card>
        <div className="row" style={{ marginBottom: 12 }}>
          <input placeholder="Search SID, name, host" value={filter.text} onChange={(e) => setFilter({ ...filter, text: e.target.value })} aria-label="Search" />
          <select value={filter.landscape} onChange={(e) => setFilter({ ...filter, landscape: e.target.value })} aria-label="Landscape">
            <option value="">All landscapes</option>{landscapes.map((l) => <option key={l}>{l}</option>)}
          </select>
          <select value={filter.product} onChange={(e) => setFilter({ ...filter, product: e.target.value })} aria-label="Product">
            <option value="">All products</option>
            {catalogue.data?.products.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
          <select value={filter.environment} onChange={(e) => setFilter({ ...filter, environment: e.target.value })} aria-label="Environment">
            <option value="">All environments</option>
            {catalogue.data?.environments.map((e) => <option key={e.key} value={e.key}>{e.label}</option>)}
          </select>
        </div>
        <DataTable<SapSystem>
          rows={rows}
          onRowClick={(s) => navigate(`/systems/${s.id}`)}
          rowKey={(s) => s.id}
          empty={data?.length ? "No systems match the filters." : "No systems yet — add one to get started."}
          columns={[
            { key: "sid", label: "SID", render: (s) => <Link to={`/systems/${s.id}`}><strong>{s.sid}</strong></Link> },
            { key: "name", label: "Name" },
            { key: "product", label: "Product", render: (s) => <>{s.product_label}{s.product_version ? <span className="muted"> · {s.product_version}</span> : null}</> },
            { key: "stack", label: "Stack / DB", render: (s) => `${s.stack} · ${s.database}` },
            { key: "environment", label: "Env", render: (s) => <span className="tag">{s.environment}</span> },
            { key: "landscape", label: "Landscape" },
            { key: "license", label: "Licensing", render: (s) => s.effective_license_model === "FUE" ? "FUE" : "Named user" },
            { key: "enabled", label: "Status", render: (s) => <Pill value={s.enabled ? "enabled" : "disabled"} /> },
          ]}
        />
      </Card>
      <SystemForm
        open={open}
        catalogue={catalogue.data}
        landscapes={landscapes}
        onClose={() => setParams({})}
        onSaved={(s) => { setParams({}); reload(); navigate(`/systems/${s.id}`); }}
      />
    </>
  );
}
