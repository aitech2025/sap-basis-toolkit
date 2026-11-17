import { useState } from "react";
import { api } from "../../api";
import { Card, DataTable, ErrorBanner, FileField, Pill, Tile, fmtDate, fmtDay, useAsync } from "../../components/ui";
import type { EndpointStatus, ImportedCertificate, SystemDetail } from "../../types";

const ABAP_PSES = ["SSL server Standard", "SSL client Standard", "SSL client Anonymous", "System PSE", "SAML2 Local Provider", "WS Security Standard"];
const JAVA_VIEWS = ["ICM_SSL", "TrustedCAs", "saml2-idp", "WebServiceSecurity"];

export default function CertificatesTab({ system }: { system: SystemDetail }) {
  const { data, error, reload } = useAsync(() => api.certificates(system.id), [system.id]);
  const [endpoint, setEndpoint] = useState({ name: "", hostname: system.host ?? "", port: 443 });
  const [pse, setPse] = useState("");
  const [file, setFile] = useState<ArrayBuffer | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const pses = system.stack === "JAVA" ? JAVA_VIEWS : ABAP_PSES;

  const run = async (fn: () => Promise<unknown>, ok: string | null) => {
    setMessage(null);
    try {
      await fn();
      if (ok) setMessage({ ok: true, text: ok });
      reload();
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    }
  };

  return (
    <>
      <ErrorBanner error={error} />
      {message && <div className={`banner ${message.ok ? "ok" : "error"}`}>{message.text}</div>}
      <div className="tiles">
        {["healthy", "warning", "critical", "expired", "unreachable", "pending"].map((k) => (
          <Tile key={k} label={k[0].toUpperCase() + k.slice(1)} value={data?.counts[k] ?? 0} />
        ))}
      </div>
      <Card title="HTTPS endpoints (live TLS checks)">
        <DataTable<EndpointStatus> rows={data?.endpoints ?? []} rowKey={(e) => e.target_id}
          empty="No endpoints yet. Add the ICM / Web Dispatcher HTTPS listeners of this system."
          columns={[
            { key: "name", label: "Name" },
            { key: "hostname", label: "Endpoint", render: (e) => `${e.hostname}:${e.port}` },
            { key: "status", label: "Status", render: (e) => <Pill value={e.status} /> },
            { key: "days_remaining", label: "Days left", numeric: true, render: (e) => e.days_remaining ?? "–" },
            { key: "not_after", label: "Expires", render: (e) => fmtDay(e.not_after) },
            { key: "checked_at", label: "Last check", render: (e) => e.error && e.status !== "healthy" ? <span title={e.error}>{fmtDate(e.checked_at)} ⓘ</span> : fmtDate(e.checked_at) },
            { key: "actions", label: "", render: (e) => (
              <div className="actions">
                <button className="btn small" disabled={busy === e.target_id}
                        onClick={async () => { setBusy(e.target_id); await run(() => api.checkEndpoint(e.target_id), `Checked ${e.name}`); setBusy(null); }}>
                  {busy === e.target_id ? "Checking…" : "Check now"}</button>
                <button className="btn small danger" onClick={() => window.confirm(`Stop monitoring ${e.hostname}:${e.port}? History is kept.`) && run(() => api.disableEndpoint(e.target_id), "Endpoint disabled")}>Disable</button>
              </div>) },
          ]} />
        <form className="row" style={{ marginTop: 12 }} onSubmit={(ev) => { ev.preventDefault(); run(() => api.addEndpoint({ ...endpoint, system_id: system.id }), "Endpoint added"); }}>
          <label className="field"><span>Name</span><input required value={endpoint.name} onChange={(e) => setEndpoint({ ...endpoint, name: e.target.value })} placeholder={`${system.sid} ICM HTTPS`} /></label>
          <label className="field"><span>Hostname</span><input required value={endpoint.hostname} onChange={(e) => setEndpoint({ ...endpoint, hostname: e.target.value })} /></label>
          <label className="field"><span>Port</span><input type="number" min={1} max={65535} value={endpoint.port} onChange={(e) => setEndpoint({ ...endpoint, port: Number(e.target.value) })} style={{ width: 100 }} /></label>
          <button className="btn primary" type="submit">Add endpoint</button>
        </form>
        <p className="muted">Hostnames and ports must be allowed by the server's ALLOWED_TARGET_HOSTS / PORTS policy.</p>
      </Card>
      <Card title={system.stack === "JAVA" ? "Key storage certificates" : "STRUST / PSE certificates"}>
        <DataTable<ImportedCertificate> rows={data?.strust_certificates ?? []} rowKey={(c) => c.id}
          empty="No certificates imported yet."
          columns={[
            { key: "pse_name", label: system.stack === "JAVA" ? "View" : "PSE" },
            { key: "subject", label: "Subject" },
            { key: "status", label: "Status", render: (c) => <Pill value={c.status} /> },
            { key: "days_remaining", label: "Days left", numeric: true },
            { key: "not_after", label: "Expires", render: (c) => fmtDay(c.not_after) },
            { key: "del", label: "", render: (c) => <button className="btn small danger" onClick={() => window.confirm("Remove this certificate from the inventory?") && run(() => api.deleteCertificate(system.id, c.id), "Certificate removed")}>Remove</button> },
          ]} />
        <form className="row" style={{ marginTop: 12 }} onSubmit={(ev) => {
          ev.preventDefault();
          if (!file) return setMessage({ ok: false, text: "Choose a certificate file" });
          run(async () => {
            const r = await api.importCertificate(system.id, pse, file);
            setMessage({ ok: true, text: `Imported ${r.imported}, skipped ${r.duplicates_skipped} duplicate(s)` });
          }, null);
        }}>
          <label className="field"><span>{system.stack === "JAVA" ? "Key storage view" : "PSE"}</span>
            <input required list="pse-list" value={pse} onChange={(e) => setPse(e.target.value)} />
            <datalist id="pse-list">{pses.map((p) => <option key={p} value={p} />)}</datalist></label>
          <FileField label="Certificate (PEM, DER or Base64)" binary onText={(c) => setFile(c as ArrayBuffer | null)} />
          <button className="btn primary" type="submit">Import</button>
        </form>
        <p className="muted">Public certificates only — files containing private keys are rejected.</p>
      </Card>
    </>
  );
}
