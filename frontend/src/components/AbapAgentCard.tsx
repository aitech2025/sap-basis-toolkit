import { useState } from "react";
import { api } from "../api";
import { RequireRole } from "./session";
import { Card, ErrorBanner, Pill, fmtDate, useAsync } from "./ui";
import type { AgentToken, SystemDetail } from "../types";

/** ABAP push agent: a background job in the SAP system that posts ABAP signals to Sentinel with a per-system token. */
export function AbapAgentCard({ system }: { system: SystemDetail }) {
  const { data, error, reload } = useAsync(() => api.agentStatus(system.id), [system.id]);
  const [issued, setIssued] = useState<AgentToken | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  if (system.stack === "JAVA") return null;
  const active = data?.active;
  const lastSeen = active?.last_seen_at ? Date.parse(active.last_seen_at) : null;
  const silent = lastSeen != null && Date.now() - lastSeen > 15 * 60_000;
  const endpoint = `${window.location.origin}${data?.endpoint ?? "/api/v1/agent/ingest"}`;

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try { await fn(); reload(); } catch (e) { setMessage({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  };

  return (
    <Card title={<>ABAP push agent {active ? <Pill value={!lastSeen ? "pending" : silent ? "warning" : "healthy"} /> : null}</>}
      actions={<button className="btn small" onClick={() => act(api.downloadAbapSources)}>Download ABAP sources</button>}>
      <ErrorBanner error={error} />
      {message && <div className={`banner ${message.ok ? "ok" : "error"}`}>{message.text}</div>}
      <p className="muted">
        A background job in {system.sid} (report Z_SENTINEL_PUSH) reads ST22, SM37, SM13, SM12, SM58, SMQ1/SMQ2 and IDoc status with ABAP
        authorisations and pushes them here every minute. It works on any database and needs no inbound access to SAP.
      </p>
      {active ? (
        <table>
          <tbody>
            <tr><th>Token</th><td><code>{active.hint}…</code> issued {fmtDate(active.created_at)}{active.created_by ? ` by ${active.created_by}` : ""}</td></tr>
            <tr><th>Last push</th><td>{active.last_seen_at ? `${fmtDate(active.last_seen_at)} from ${active.last_ip ?? "?"} (agent ${active.last_agent_version ?? "?"}, ${Math.round((active.last_payload_bytes ?? 0) / 1024)} KB)` : "Not yet received"}</td></tr>
          </tbody>
        </table>
      ) : <p>No token issued for {system.sid} yet.</p>}
      {issued?.token && (
        <div className="banner info">
          <strong>Copy this token now; it is not shown again.</strong>
          <pre className="mt-2 select-all">{issued.token}</pre>
        </div>
      )}
      <RequireRole role="admin">
        <div className="actions" style={{ marginTop: 12 }}>
          <button className="btn primary" disabled={busy} onClick={() => act(async () => {
            if (active && !window.confirm(`Issue a new token for ${system.sid}? The current token stops working immediately.`)) return;
            setIssued(await api.issueAgentToken(system.id));
          })}>{active ? "Rotate token" : "Issue token"}</button>
          {active && <button className="btn danger" disabled={busy} onClick={() => act(async () => {
            if (!window.confirm(`Revoke the agent token of ${system.sid}? Pushes are rejected until a new token is issued.`)) return;
            await api.revokeAgentToken(system.id, active.id);
            setIssued(null);
          })}>Revoke</button>}
        </div>
      </RequireRole>
      <details className="mt-3">
        <summary className="cursor-pointer font-medium">Set up in {system.sid}</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm">
          <li>Import the ABAP sources with abapGit (online from the repository folder <code>abap/</code>, or offline with the ZIP above) into a Z package, and activate.</li>
          <li>SM59: create HTTP destination <code>SENTINEL</code> (type G) to <code>{endpoint}</code>, with SSL and the Sentinel server certificate in STRUST.</li>
          <li>Run Z_SENTINEL_PUSH once with <em>Collect only</em> to check what can be read, then save a variant with the destination and the token.</li>
          <li>SM36: schedule Z_SENTINEL_PUSH with that variant every 1 to 5 minutes, under a system user with read access to SNAP, TBTCO, VBHDR, ARFCSSTATE, TRFCQOUT/TRFCQIN and EDIDC (and S_RFC on /SDF/GET_DUMP_LOG, ENQUEUE_READ).</li>
        </ol>
        <p className="muted small">A failed push ends the job with an error (visible in SM37). If no push arrives for 15 minutes, Sentinel opens an incident.</p>
      </details>
    </Card>
  );
}
