import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { useSession } from "../../components/session";
import { Card, DataTable, ErrorBanner, Pill, fmtDate, useAsync } from "../../components/ui";
import type { ControlPlan, ControlScope, Operation, ProcedureStep, SapInstance, SystemDetail } from "../../types";
import { ROLES, statusPill } from "../Discover";

type Action = "start" | "stop" | "restart";
interface Pending { action: Action; scope: ControlScope; instance?: SapInstance }

const SCOPES: { scope: ControlScope; label: string; hint: string }[] = [
  { scope: "system", label: "SAP system + HANA database", hint: "Full SAP standard sequence including the database" },
  { scope: "app", label: "SAP application only", hint: "ASCS/SCS, ERS, PAS, AAS, Web Dispatcher; the database stays as it is" },
  { scope: "db", label: "HANA database only", hint: "HDB instance; stopping is blocked while application servers run" },
];
const STEP_ICON: Record<string, [string, string]> = {
  ok: ["✓", "good"], confirmed: ["✓", "good"], warning: ["▲", "warn"], overridden: ["▲", "warn"], not_confirmed: ["!", "warn"],
  failed: ["✕", "bad"], timeout: ["✕", "bad"], aborted: ["■", "bad"], running: ["◐", "accent"], pending: ["○", "muted"],
  skipped: ["–", "muted"], todo: ["○", "muted"],
};
const STEP_TEXT: Record<string, string> = {
  ok: "done", confirmed: "confirmed", warning: "warning", overridden: "overridden", not_confirmed: "not confirmed",
  failed: "failed", timeout: "timed out", aborted: "aborted", running: "running", pending: "pending", skipped: "skipped", todo: "to do",
};
const cap = (s: string) => s[0].toUpperCase() + s.slice(1);

export function StepList({ steps, onConfirm }: { steps: ProcedureStep[]; onConfirm?: (key: string) => void }) {
  return (
    <ol className="steps">
      {steps.map((s) => {
        const [icon, cls] = STEP_ICON[s.status] ?? ["○", "muted"];
        return (
          <li key={s.key} className={`step-row ${s.kind} ${s.status}`}>
            <span className={`step-icon ${cls}`} aria-hidden="true">{icon}</span>
            <div className="step-body">
              <div><strong>{s.title}</strong> <span className={`step-status ${cls}`}>{STEP_TEXT[s.status] ?? s.status}</span>
                {s.kind === "manual" && <span className="tag">manual</span>}
                {s.blocking && s.kind === "check" && <span className="tag">blocking</span>}</div>
              {s.detail && <div className="muted small">{s.detail}</div>}
              {s.instances && Object.keys(s.instances).length > 0 && (
                <div className="chips">{Object.entries(s.instances).map(([k, v]) => <span key={k} className={`inst-chip ${v}`}>{k}: {v}</span>)}</div>
              )}
              {onConfirm && s.status === "todo" && <button className="btn small" onClick={() => onConfirm(s.key)}>Mark done</button>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function ProcedureView({ op, onAbort, onConfirm }: { op: Operation; onAbort?: () => void; onConfirm?: (key: string) => void }) {
  return (
    <div className="procedure">
      <div className="procedure-head">
        <div>
          <Pill value={op.status} /> <strong>{cap(op.action)}</strong> · {op.scope === "instance" ? `${op.hostname} ${op.instance_nr}` : op.scope_label}
          <div className="muted small">{op.requested_by} · {fmtDate(op.started_at)} · “{op.reason}”{op.force ? " · forced" : ""}</div>
        </div>
        {onAbort && op.status === "running" && (
          <button className="btn danger small" disabled={op.abort_requested} onClick={onAbort}>{op.abort_requested ? "Aborting…" : "Abort"}</button>
        )}
      </div>
      <p className={op.status === "failed" || op.status === "timeout" || op.status === "aborted" ? "danger-note" : "muted"} aria-live="polite">{op.message}</p>
      <StepList steps={op.steps} onConfirm={op.status !== "running" ? onConfirm : undefined} />
    </div>
  );
}

export default function InstancesTab({ system }: { system: SystemDetail }) {
  const { can } = useSession();
  const instances = useAsync(() => api.instances(system.id), [system.id]);
  const status = useAsync(() => api.controlStatus(system.id), [system.id]);
  const ops = useAsync(() => api.operations(system.id), [system.id]);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [shown, setShown] = useState<Operation | null>(null);

  const current = shown ?? status.data?.running ?? ops.data?.[0] ?? null;
  const runningId = current?.status === "running" ? current.id : null;
  useEffect(() => {
    if (!runningId) return;
    const timer = window.setInterval(async () => {
      try {
        const op = await api.operation(runningId);
        setShown(op);
        if (op.status !== "running") { instances.reload(); status.reload(); ops.reload(); }
        else instances.reload();
      } catch { /* keep polling */ }
    }, 2500);
    return () => window.clearInterval(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningId]);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try { await fn(); } catch (err) { setError((err as Error).message); }
  };
  const refresh = () => run(async () => {
    setRefreshing(true);
    try { instances.setData(await api.refreshInstances(system.id)); } finally { setRefreshing(false); }
  });
  const changeRole = (inst: SapInstance, role: string) => run(async () => {
    const updated = await api.setInstanceRole(system.id, inst.id, role);
    instances.setData((instances.data ?? []).map((i) => (i.id === updated.id ? updated : i)));
  });
  const abort = () => current && window.confirm("Abort the procedure? Instances already started/stopped stay as they are.")
    && run(async () => setShown(await api.abortOperation(current.id)));
  const confirmStep = (key: string) => current && run(async () => { setShown(await api.confirmStep(current.id, key)); ops.reload(); });

  const caps = status.data;
  const admin = can("admin");
  const busy = !!runningId;
  const buttons = (scope: ControlScope, instance?: SapInstance) => (
    <div className="actions">
      {(["start", "stop", "restart"] as Action[]).map((a) => (
        <button key={a} className={`btn small${a === "stop" ? " danger" : ""}`}
                disabled={!caps?.allowed || !caps.scopes[scope] || busy}
                onClick={() => setPending({ action: a, scope, instance })}>{cap(a)}</button>
      ))}
    </div>
  );

  const rows = instances.data ?? [];
  const controllable = (i: SapInstance) => i.role !== "SMDA" && i.role !== "OTHER";
  return (
    <>
      <ErrorBanner error={error || instances.error || status.error} />
      <Card title="Controlled start / stop">
        {caps && !caps.allowed && <div className="banner info">{caps.reason}</div>}
        {caps?.allowed && !admin && <div className="banner info">Only administrators can start or stop systems.</div>}
        {caps?.notes.map((n) => <div key={n} className="banner warn">{n}</div>)}
        {admin && (
          <div className="scope-grid">
            {SCOPES.map((s) => (
              <div key={s.scope} className={`scope-row${caps?.scopes[s.scope] ? "" : " disabled"}`}>
                <div><strong>{s.label}</strong><div className="muted small">{s.hint}</div></div>
                {buttons(s.scope)}
              </div>
            ))}
          </div>
        )}
        <details className="sequence">
          <summary>SAP standard sequence</summary>
          <div className="seq-cols">
            <div><h4>Start</h4><ol>{caps?.start_sequence.map((x) => <li key={x}>{x.replace(/^Start /, "")}</li>)}</ol></div>
            <div><h4>Stop</h4><ol>{caps?.stop_sequence.map((x) => <li key={x}>{x.replace(/^Stop /, "")}</li>)}</ol></div>
          </div>
          <p className="muted small">Each group must be fully GREEN (start) or GRAY (stop) before the next one begins. Restart runs the full stop,
            then the full start. Pre-checks (pending updates, running jobs, active users, database state) run first. Follow-ups such as
            BTCTRNS2 and unlocking users are tracked after the start.</p>
        </details>
        {current && <ProcedureView op={current} onAbort={admin ? abort : undefined} onConfirm={can("operator") ? confirmStep : undefined} />}
      </Card>

      <Card title={`Instances (${rows.filter((i) => i.status === "GREEN").length}/${rows.length} running)`}
            actions={can("operator") && <button className="btn small" onClick={refresh} disabled={refreshing}>{refreshing ? "Refreshing…" : "Refresh from sapstartsrv"}</button>}>
        <DataTable<SapInstance>
          rows={rows} rowKey={(i) => i.id}
          empty={<>No instances yet. Use <a href="/discover">Discover system</a> or add a sapcontrol collector, then refresh.</>}
          columns={[
            { key: "role", label: "Role", render: (i) => can("operator") ? (
              <select aria-label={`Role of ${i.hostname} ${i.instance_nr}`} value={i.role} onChange={(e) => changeRole(i, e.target.value)}>
                {ROLES.map((r) => <option key={r}>{r}</option>)}</select>) : <span className="role">{i.role}</span> },
            { key: "instance_name", label: "Instance", render: (i) => <><strong>{i.instance_name ?? "–"}</strong> <span className="muted small">{i.role_label}</span></> },
            { key: "hostname", label: "Host" },
            { key: "instance_nr", label: "Nr" },
            { key: "features", label: "Features", render: (i) => <span className="muted small">{i.features}</span> },
            { key: "status", label: "Status", render: (i) => statusPill(i.status) },
            { key: "seen", label: "Last seen", render: (i) => fmtDate(i.last_seen_at) },
            ...(admin ? [{ key: "control", label: "Control", render: (i: SapInstance) => controllable(i) ? buttons("instance", i) : <span className="muted small">not controlled</span> }] : []),
          ]}
        />
      </Card>

      <Card title="Procedure history">
        <DataTable<Operation>
          rows={ops.data ?? []} rowKey={(o) => o.id} empty="No start/stop procedures yet." onRowClick={(o) => setShown(o)}
          columns={[
            { key: "started_at", label: "Started", render: (o) => fmtDate(o.started_at) },
            { key: "action", label: "Action", render: (o) => <strong>{o.action}</strong> },
            { key: "target", label: "Scope", render: (o) => o.scope === "instance" ? `${o.hostname} · ${o.instance_nr}` : o.scope_label },
            { key: "requested_by", label: "By" },
            { key: "reason", label: "Reason" },
            { key: "status", label: "Result", render: (o) => <Pill value={o.status} /> },
            { key: "message", label: "Detail", render: (o) => <span className="muted small">{o.message}</span> },
          ]}
        />
      </Card>

      <ControlDialog system={system} pending={pending} onClose={() => setPending(null)}
                     onStarted={(op) => { setPending(null); setShown(op); ops.reload(); status.reload(); }} />
    </>
  );
}

function ControlDialog({ system, pending, onClose, onStarted }: {
  system: SystemDetail; pending: Pending | null; onClose: () => void; onStarted: (op: Operation) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [plan, setPlan] = useState<ControlPlan | null>(null);
  const [form, setForm] = useState({ confirm: "", reason: "", soft: "", maintenance: "30", force: false });
  const [checklist, setChecklist] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (!pending) { if (d.open) d.close(); return; }
    setPlan(null);
    setChecklist({});
    setError(null);
    setForm({ confirm: "", reason: "", soft: "", maintenance: pending.action === "start" ? "0" : "30", force: false });
    if (!d.open) d.showModal();
    api.controlPlan(system.id, { action: pending.action, scope: pending.scope,
      hostname: pending.instance?.hostname, instance_nr: pending.instance?.instance_nr })
      .then(setPlan).catch((e: Error) => setError(e.message));
  }, [pending, system.id]);
  if (!pending) return <dialog ref={ref} onClose={onClose} />;
  const target = pending.scope === "instance"
    ? `${pending.instance?.instance_name ?? pending.instance?.role} on ${pending.instance?.hostname}`
    : SCOPES.find((s) => s.scope === pending.scope)?.label;
  const stopping = pending.action !== "start";
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      onStarted(await api.control(system.id, {
        action: pending.action, scope: pending.scope, hostname: pending.instance?.hostname ?? null,
        instance_nr: pending.instance?.instance_nr ?? null, soft_timeout: form.soft === "" ? null : Number(form.soft),
        confirm_sid: form.confirm, reason: form.reason, maintenance_minutes: Number(form.maintenance) || null,
        checklist, force: form.force,
      }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const blocked = !!plan?.blocked && !form.force;
  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="control-title" className="wide-dialog">
      <form onSubmit={submit}>
        <div className="dialog-body">
          <h2 id="control-title" style={{ marginBottom: 6 }}>{cap(pending.action)} {system.sid}: {target}</h2>
          {stopping && system.environment === "PRD" && <p className="danger-note">Production system: users will be disconnected.</p>}
          <ErrorBanner error={error} />
          {!plan && !error && <p className="muted">Running pre-checks…</p>}
          {plan && (
            <>
              {plan.blocked && (
                <div className="banner error" role="alert"><strong>Blocked by pre-checks:</strong>
                  <ul className="plain">{plan.blockers.map((b) => <li key={b}>{b}</li>)}</ul></div>
              )}
              <h3 className="small-head">Procedure</h3>
              <StepList steps={plan.steps.filter((s) => s.kind !== "manual" || !s.key.startsWith("pre_"))} />
              {plan.manual.length > 0 && (
                <>
                  <h3 className="small-head">Before the stop — confirm what has been done</h3>
                  {plan.manual.map((m) => (
                    <label key={m.key} className="field check">
                      <input type="checkbox" checked={!!checklist[m.key]} onChange={(e) => setChecklist({ ...checklist, [m.key]: e.target.checked })} />
                      <span>{m.title}</span></label>
                  ))}
                  <p className="muted small">Unconfirmed items are recorded as warnings in the procedure log.</p>
                </>
              )}
            </>
          )}
          <div className="form-grid" style={{ marginTop: 12 }}>
            {stopping && pending.scope !== "db" && (
              <label className="field"><span>Soft timeout (s)</span>
                <input inputMode="numeric" value={form.soft} placeholder="SAP default" onChange={(e) => setForm({ ...form, soft: e.target.value.replace(/\D/g, "") })} />
                <small>Time for running work to finish before a hard stop.</small></label>
            )}
            <label className="field"><span>Maintenance window</span>
              <select value={form.maintenance} onChange={(e) => setForm({ ...form, maintenance: e.target.value })}>
                <option value="0">None</option>{[15, 30, 60, 120, 240].map((m) => <option key={m} value={m}>{m} min (suppress alerts)</option>)}
              </select></label>
            <label className="field wide"><span>Reason</span>
              <input required minLength={5} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="Kernel patch CHG0012345" /></label>
            {plan?.blocked && (
              <label className="field check wide"><input type="checkbox" checked={form.force} onChange={(e) => setForm({ ...form, force: e.target.checked })} />
                <span className="danger-note">Override the blocking pre-checks (recorded in the audit trail)</span></label>
            )}
            <label className="field wide"><span>Type <strong>{system.sid}</strong> to confirm</span>
              <input required value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value.toUpperCase() })} autoComplete="off" /></label>
          </div>
        </div>
        <div className="dialog-foot">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className={`btn ${pending.action === "start" ? "primary" : "danger"}`}
                  disabled={saving || !plan || blocked || form.confirm !== system.sid || form.reason.trim().length < 5}>
            {saving ? "Sending…" : `Run ${pending.action} procedure`}</button>
        </div>
      </form>
    </dialog>
  );
}
