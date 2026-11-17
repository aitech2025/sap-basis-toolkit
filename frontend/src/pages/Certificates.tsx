import { Link } from "react-router-dom";
import { api } from "../api";
import { Card, DataTable, ErrorBanner, Pill, Tile, fmtDay, useAsync } from "../components/ui";
import type { EndpointStatus, ImportedCertificate } from "../types";

export default function Certificates() {
  const { data, error } = useAsync(() => api.certificates(), []);
  const sys = (id: number | null, sid: string | null, landscape: string | null) =>
    id ? <Link to={`/systems/${id}/certificates`}>{sid}<span className="muted"> · {landscape}</span></Link> : <span className="muted">unassigned</span>;
  return (
    <>
      <div className="page-head"><div><h1>Certificates</h1><p>Every endpoint and PSE certificate across the landscape, soonest expiry first.</p></div></div>
      <ErrorBanner error={error} />
      <div className="tiles">
        {["critical", "expired", "warning", "unreachable", "healthy"].map((k) => <Tile key={k} label={k[0].toUpperCase() + k.slice(1)} value={data?.counts[k] ?? 0} />)}
      </div>
      <Card title="Endpoints">
        <DataTable<EndpointStatus> rows={data?.endpoints ?? []} rowKey={(e) => e.target_id} empty="No endpoints monitored."
          columns={[
            { key: "sid", label: "System", render: (e) => sys(e.system_id, e.sid, e.landscape) },
            { key: "name", label: "Name" },
            { key: "hostname", label: "Endpoint", render: (e) => `${e.hostname}:${e.port}` },
            { key: "status", label: "Status", render: (e) => <Pill value={e.status} /> },
            { key: "days_remaining", label: "Days left", numeric: true, render: (e) => e.days_remaining ?? "–" },
            { key: "not_after", label: "Expires", render: (e) => fmtDay(e.not_after) },
          ]} />
      </Card>
      <Card title="STRUST / key storage certificates">
        <DataTable<ImportedCertificate> rows={data?.strust_certificates ?? []} rowKey={(c) => c.id} empty="No certificates imported."
          columns={[
            { key: "sid", label: "System", render: (c) => sys(c.system_id, c.sid, c.landscape) },
            { key: "pse_name", label: "PSE / view" },
            { key: "subject", label: "Subject" },
            { key: "status", label: "Status", render: (c) => <Pill value={c.status} /> },
            { key: "days_remaining", label: "Days left", numeric: true },
            { key: "not_after", label: "Expires", render: (c) => fmtDay(c.not_after) },
          ]} />
      </Card>
    </>
  );
}
