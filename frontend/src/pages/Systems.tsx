import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { SystemForm } from "../components/SystemForm";
import { RequireRole } from "../components/session";
import { Plus, Radar, Search } from "lucide-react";
import { Card, DataTable, ErrorBanner, PageHeader, Pill, useAsync } from "../components/ui";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
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
      <PageHeader title="SAP systems" description="ECC, S/4HANA, BW/4HANA, NetWeaver ABAP and Java systems under monitoring."
        actions={<>
          <Button variant="outline" onClick={() => setParams({ new: "1" })}><Plus />Add manually</Button>
          <RequireRole role="admin"><Button asChild><Link to="/discover" className="hover:no-underline"><Radar />Discover from host</Link></Button></RequireRole>
        </>} />
      <ErrorBanner error={error || catalogue.error} />
      <Card>
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <div className="relative w-full sm:w-72">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input className="pl-9" placeholder="Search SID, name or host" value={filter.text} onChange={(e) => setFilter({ ...filter, text: e.target.value })} aria-label="Search" />
          </div>
          <select className="filter-select !w-auto" value={filter.landscape} onChange={(e) => setFilter({ ...filter, landscape: e.target.value })} aria-label="Landscape">
            <option value="">All landscapes</option>{landscapes.map((l) => <option key={l}>{l}</option>)}
          </select>
          <select className="filter-select !w-auto" value={filter.product} onChange={(e) => setFilter({ ...filter, product: e.target.value })} aria-label="Product">
            <option value="">All products</option>
            {catalogue.data?.products.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
          <select className="filter-select !w-auto" value={filter.environment} onChange={(e) => setFilter({ ...filter, environment: e.target.value })} aria-label="Environment">
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
            { key: "sid", label: "SID", render: (s) => <Link to={`/systems/${s.id}`} className="font-mono font-semibold">{s.sid}</Link> },
            { key: "name", label: "Name" },
            { key: "product", label: "Product", render: (s) => <>{s.product_label}{s.product_version ? <span className="muted"> · {s.product_version}</span> : null}</> },
            { key: "stack", label: "Stack / DB", render: (s) => `${s.stack} · ${s.database}` },
            { key: "environment", label: "Env", render: (s) => <Badge variant={s.environment === "PRD" ? "bad" : "secondary"} className="font-mono">{s.environment}</Badge> },
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
