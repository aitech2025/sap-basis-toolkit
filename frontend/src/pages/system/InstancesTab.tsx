import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { useSession } from "../../components/session";
import { Card, DataTable, ErrorBanner, Pill, fmtDate, useAsync } from "../../components/ui";
import type { Operation, SapInstance, SystemDetail } from "../../types";
import { ROLES, statusPill } from "../Discover";

type Action = "start" | "stop" | "restart";
interface Pending { action: Action; scope: "system" | "instance"; instance?: SapInstance }

const OPTION_LABELS: Record<string, string> = {
  ALL: "All instances (in start-priority order)", SCS: "Central services only (ASCS/SCS, ERS)",
  DIALOG: "Application servers only (PAS/AAS/Java)", ABAP: "ABAP instances", J2EE: "Java instances",
};

function OperationView({ op }: { op: Operation }) {
  const last = op.progress[op.progress.length - 1];
  return (
    <div>
      <p><Pill value={op.status} /> <strong>{op.action}</strong> {op.scope === "system" ? `system (${op.options})` : `${op.hostname} ${op.instance_nr}`}
        {" "}· {op.requested_by} · {fmtDate(op.started_at)}</p>
      <p className="muted">{op.message}</p>
      {last?.instances && (
        <div className="op-timeline" aria-live="polite">
          <div className="step"><time>{new Date(last.at).toLocaleTimeString()}</time>
            {Object.entries(last.instances).map(([k, v]) => <span key={k} className={`inst-chip ${v}`}>{k}: {v}</span>)}</div>
        </div>
      )}
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
  const [live, setLive] = useState<Operation | null>(null);

  // poll a running operation until it finishes
  const running = live?.status === "running" ? live : status.data?.running ?? null;
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(async () => {
      try {
        const op = await api.operation(running.id);
        setLive(op);
        if (op.status !== "running") {
          instances.reload(); status.reload(); ops.reload();
        }
      } catch { /* keep polling */ }
    }, 2500);
    return () => window.clearInterval(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running?.id]);

  const refresh = async () => {
    setRefreshing(true);
    setError(null);
    try {
      instances.setData(await api.refreshInstances(system.id));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRefreshing(false);
    }
  };

  const changeRole = async (inst: SapInstance, role: string) => {
    try {
      const updated = await api.setInstanceRole(system.id, inst.id, role);
      instances.setData((instances.data ?? []).map((i) => (i.id === updated.id ? updated : i)));
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const allowed = !!status.data?.allowed && can("admin");
  const busy = !!running;
  const controlButtons = (scope: "system" | "instance", instance?: SapInstance) => (
    <div className="actions">
      {(["start", "stop", "restart"] as Action[]).map((a) => (
        <button key={a} className={`btn small${a === "stop" ? " danger" : ""}`} disabled={!allowed || busy}
                onClick={() => setPending({ action: a, scope, instance })}>{a[0].toUpperCase() + a.slice(1)}</button>
      ))}
    </div>
  );

  const rows = instances.data ?? [];
  const up = rows.filter((i) => i.status === "GREEN").length;
  return (
    <>
      <ErrorBanner error={error || instances.error || status.error} />
      <Card title={`Instances (${up}/${rows.length} running)`}
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
            { key: "start_priority", label: "Start prio", render: (i) => i.start_priority ?? "–" },
            { key: "features", label: "Features", render: (i) => <span className="muted small">{i.features}</span> },
            { key: "status", label: "Status", render: (i) => statusPill(i.status) },
            { key: "seen", label: "Last seen", render: (i) => fmtDate(i.last_seen_at) },
            ...(can("admin") ? [{ key: "control", label: "Control", render: (i: SapInstance) => controlButtons("instance", i) }] : []),
          ]}
        />
      </Card>

      <Card title="System start / stop">
        {status.data && !status.data.allowed && <div className="banner info">{status.data.reason}</div>}
        {status.data?.allowed && !can("admin") && <div className="banner info">Only administrators can start or stop systems.</div>}
        <p className="muted small">{status.data?.database_note} Operations wait up to {Math.round((status.data?.timeout_seconds ?? 1200) / 60)} min
          for every instance to reach the target state and are recorded in the audit log.</p>
        {can("admin") && controlButtons("system")}
        {running && <div style={{ marginTop: 12 }}><OperationView op={live?.id === running.id ? live : running} /></div>}
      </Card>

      <Card title="Operation history">
        <DataTable<Operation>
          rows={ops.data ?? []} rowKey={(o) => o.id} empty="No start/stop operations yet."
          columns={[
            { key: "started_at", label: "Started", render: (o) => fmtDate(o.started_at) },
            { key: "action", label: "Action", render: (o) => <strong>{o.action}</strong> },
            { key: "target", label: "Target", render: (o) => o.scope === "system" ? `system · ${o.options}` : `${o.hostname} · ${o.instance_nr}` },
            { key: "requested_by", label: "By" },
            { key: "reason", label: "Reason" },
            { key: "status", label: "Result", render: (o) => <Pill value={o.status} /> },
            { key: "message", label: "Detail", render: (o) => <span className="muted small">{o.message}</span> },
          ]}
        />
      </Card>

      <ControlDialog system={system} pending={pending} options={status.data?.options ?? ["ALL"]}
                     onClose={() => setPending(null)}
                     onStarted={(op) => { setPending(null); setLive(op); ops.reload(); status.reload(); }} />
    </>
  );
}

function ControlDialog({ system, pending, options, onClose, onStarted }: {
  system: SystemDetail; pending: Pending | null; options: string[]; onClose: () => void; onStarted: (op: Operation) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [form, setForm] = useState({ confirm: "", reason: "", option: "ALL", soft: "", maintenance: "30" });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (pending) {
      setForm({ confirm: "", reason: "", option: "ALL", soft: "", maintenance: pending.action === "start" ? "0" : "30" });
      setError(null);
      if (!d.open) d.showModal();
    } else if (d.open) d.close();
  }, [pending]);
  if (!pending) return <dialog ref={ref} onClose={onClose} />;
  const target = pending.scope === "system" ? `${system.sid} (${OPTION_LABELS[form.option] ?? form.option})`
    : `${pending.instance?.instance_name ?? ""} on ${pending.instance?.hostname} (${pending.instance?.role})`;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      onStarted(await api.control(system.id, {
        action: pending.action, scope: pending.scope, options: pending.scope === "system" ? form.option : null,
        hostname: pending.instance?.hostname ?? null, instance_nr: pending.instance?.instance_nr ?? null,
        soft_timeout: form.soft === "" ? null : Number(form.soft), confirm_sid: form.confirm, reason: form.reason,
        maintenance_minutes: Number(form.maintenance) || null,
      }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="control-title">
      <form onSubmit={submit}>
        <div className="dialog-body">
          <h2 id="control-title" style={{ marginBottom: 10 }}>{pending.action[0].toUpperCase() + pending.action.slice(1)} {target}</h2>
          {pending.action !== "start" && system.environment === "PRD" && <p className="danger-note">This is a production system. Users will be disconnected.</p>}
          <ErrorBanner error={error} />
          <div className="form-grid">
            {pending.scope === "system" && (
              <label className="field wide"><span>Instances</span>
                <select value={form.option} onChange={(e) => setForm({ ...form, option: e.target.value })}>
                  {options.map((o) => <option key={o} value={o}>{OPTION_LABELS[o] ?? o}</option>)}</select></label>
            )}
            {pending.action !== "start" && (
              <label className="field"><span>Soft timeout (s)</span>
                <input inputMode="numeric" value={form.soft} placeholder="SAP default" onChange={(e) => setForm({ ...form, soft: e.target.value.replace(/\D/g, "") })} />
                <small>Time to let running work finish before a hard stop.</small></label>
            )}
            <label className="field"><span>Maintenance window</span>
              <select value={form.maintenance} onChange={(e) => setForm({ ...form, maintenance: e.target.value })}>
                <option value="0">None</option>{[15, 30, 60, 120, 240].map((m) => <option key={m} value={m}>{m} min (suppress alerts)</option>)}
              </select></label>
            <label className="field wide"><span>Reason</span>
              <input required minLength={5} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="Kernel patch CHG0012345" /></label>
            <label className="field wide"><span>Type <strong>{system.sid}</strong> to confirm</span>
              <input required value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value.toUpperCase() })} autoComplete="off" /></label>
          </div>
        </div>
        <div className="dialog-foot">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className={`btn ${pending.action === "start" ? "primary" : "danger"}`}
                  disabled={saving || form.confirm !== system.sid || form.reason.trim().length < 5}>
            {saving ? "Sending…" : `${pending.action[0].toUpperCase() + pending.action.slice(1)} now`}</button>
        </div>
      </form>
    </dialog>
  );
}
