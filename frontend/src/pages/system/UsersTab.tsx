import { useState } from "react";
import { api } from "../../api";
import { Card, DataTable, ErrorBanner, FileField, Tile, download, fmtNumber } from "../../components/ui";
import type { SystemDetail, UserRecord, UserReport } from "../../types";

export default function UsersTab({ system }: { system: SystemDetail }) {
  const caps = system.capabilities;
  const java = caps.users.source === "UME";
  const [userCsv, setUserCsv] = useState<string | null>(null);
  const [usageCsv, setUsageCsv] = useState<string | null>(null);
  const [days, setDays] = useState(90);
  const [report, setReport] = useState<UserReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const body = () => ({ usr02_csv: userCsv ?? "", usage_csv: usageCsv, dormant_days: days });

  const analyse = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!userCsv) return setError(`Choose a ${java ? "UME user" : "USR02"} export`);
    setBusy(true);
    setError(null);
    try {
      setReport(await api.analyzeUsers(system.id, body()));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const s = report?.summary;

  return (
    <>
      <Card title={`Analyse ${java ? "UME" : "USR02"} users`}>
        <form onSubmit={analyse}>
          <div className="form-grid">
            <FileField label={java ? "UME user export (CSV)" : "USR02 / SUIM export"} accept=".csv,.txt" onText={(c) => setUserCsv(c as string | null)} />
            {caps.users.license_recommendations && (
              <FileField label="ST03N usage export (optional)" accept=".csv,.txt" onText={(c) => setUsageCsv(c as string | null)} />
            )}
            <label className="field"><span>Dormant after (days)</span><input type="number" min={1} value={days} onChange={(e) => setDays(Number(e.target.value))} /></label>
          </div>
          <div className="actions" style={{ marginTop: 12 }}>
            <button className="btn primary" type="submit" disabled={busy}>{busy ? "Analysing…" : "Analyse"}</button>
            {report && <button type="button" className="btn" onClick={async () => download(`${system.sid}_lock_candidates.csv`, await api.lockListCsv(system.id, body()))}>Download lock list (CSV)</button>}
          </div>
        </form>
        <p className="muted">
          Files are analysed in memory; only summary counts are kept in History. Locking stays a reviewed admin step (SU10 / NWA).
          Licensing: <strong>{caps.license_model.label}</strong>.
        </p>
        <details><summary className="muted">How to export</summary><pre>{caps.users.how_to_export}</pre></details>
      </Card>
      <ErrorBanner error={error} />
      {s && report && (
        <>
          <div className="tiles">
            <Tile label="Users analysed" value={s.users_analysed} />
            <Tile label="Active dialog users" value={s.active_dialog_users} />
            <Tile label="Lock candidates" value={s.lock_candidates} hint={`${s.never_logged_on} never logged on`} />
            <Tile label="License downgrades" value={s.license_downgrades} />
            <Tile label="Estimated saving" value={fmtNumber(s.estimated_saving, 2)} hint={s.saving_unit} />
          </div>
          <Card title="Lock candidates">
            <DataTable<UserRecord> rows={report.lock_candidates} rowKey={(u) => u.user} empty="No dormant users found."
              columns={[
                { key: "user", label: "User", render: (u) => <strong>{u.user}</strong> },
                { key: "user_group", label: "Group" },
                { key: "last_logon", label: "Last logon" },
                { key: "days_inactive", label: "Days", numeric: true },
                { key: "current_license", label: "License" },
                { key: "reason", label: "Reason" },
              ]} />
            {report.su10_user_list && (
              <label className="field" style={{ marginTop: 12 }}><span>{java ? "User list" : "SU10 user list"}</span>
                <textarea readOnly rows={Math.min(8, report.lock_candidates.length + 1)} value={report.su10_user_list} /></label>
            )}
          </Card>
          {caps.users.license_recommendations && (
            <Card title="License downgrade candidates">
              <DataTable rows={report.license_changes} rowKey={(c) => c.user}
                empty={usageCsv ? "No downgrade candidates." : "Upload an ST03N usage export to get license recommendations."}
                columns={[
                  { key: "user", label: "User" },
                  { key: "current_license", label: "Current" },
                  { key: "recommended_license", label: "Recommended" },
                  { key: "saving", label: `Saving (${s.saving_unit})`, numeric: true },
                  { key: "transactions_used", label: "Transactions used", render: (c) => c.transactions_used.join(", ") },
                ]} />
              <p className="muted">{s.saving_note}</p>
            </Card>
          )}
        </>
      )}
    </>
  );
}
