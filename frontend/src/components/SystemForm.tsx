import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { Catalogue, SapSystem, SystemInput } from "../types";
import { useSession } from "./session";
import { ErrorBanner } from "./ui";

const TIMEZONES = ["UTC", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Asia/Dubai", "Australia/Sydney", "Europe/London",
  "Europe/Berlin", "Europe/Paris", "Europe/Zurich", "America/New_York", "America/Chicago", "America/Los_Angeles", "America/Sao_Paulo"];
const EMPTY: Partial<SystemInput> = {
  sid: "", name: "", product: "S4HANA", stack: "ABAP", database: "HANA", environment: "DEV", landscape: "default",
  product_version: "", basis_release: "", license_model: null, host: "", default_client: "", description: "", timezone: "", enabled: true, control_enabled: null,
};

export function SystemForm({ open, initial, catalogue, landscapes, onClose, onSaved }: {
  open: boolean;
  initial?: SapSystem | null;
  catalogue: Catalogue | null;
  landscapes: string[];
  onClose: () => void;
  onSaved: (s: SapSystem) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const { can } = useSession();
  const [form, setForm] = useState<Partial<SystemInput>>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open) {
      setForm(initial ? { ...initial } : EMPTY);
      setError(null);
      if (!dialog.open) dialog.showModal();
    } else if (dialog.open) dialog.close();
  }, [open, initial]);

  const product = catalogue?.products.find((p) => p.key === form.product);
  const stacks = useMemo(() => catalogue?.stacks.filter((s) => !product || product.stacks.includes(s.key)) ?? [], [catalogue, product]);
  const databases = useMemo(
    () => catalogue?.databases.filter((d) => !product?.databases || product.databases.includes(d.key)) ?? [],
    [catalogue, product],
  );

  const set = (key: keyof SystemInput, value: unknown) => setForm((f) => ({ ...f, [key]: value }));
  const changeProduct = (key: string) => {
    const p = catalogue?.products.find((x) => x.key === key);
    setForm((f) => ({
      ...f, product: key,
      stack: p && !p.stacks.includes(f.stack ?? "") ? p.stacks[0] : f.stack,
      database: p?.databases && !p.databases.includes(f.database ?? "") ? p.databases[0] : f.database,
    }));
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    const body: Partial<SystemInput> = {};
    for (const [k, v] of Object.entries(form)) {
      if (["id", "product_label", "effective_license_model", "created_at", "capabilities"].includes(k)) continue;
      (body as Record<string, unknown>)[k] = v === "" ? null : v;
    }
    if (!can("admin")) delete body.control_enabled;  // admin-only setting
    try {
      const saved = initial ? await api.updateSystem(initial.id, body) : await api.createSystem(body);
      onSaved(saved);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const licenseDefault = catalogue?.license_models.find((m) => m.key === product?.license_model);

  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="system-form-title">
      <form onSubmit={submit}>
        <div className="dialog-body">
          <h2 id="system-form-title" style={{ marginBottom: 14 }}>{initial ? `Edit ${initial.sid}` : "Register SAP system"}</h2>
          <ErrorBanner error={error} />
          <div className="form-grid">
            <label className="field"><span>SID</span>
              <input required maxLength={3} value={form.sid ?? ""} onChange={(e) => set("sid", e.target.value.toUpperCase())} placeholder="PRD" /></label>
            <label className="field"><span>Name</span>
              <input required value={form.name ?? ""} onChange={(e) => set("name", e.target.value)} placeholder="S/4HANA Finance production" /></label>
            <label className="field"><span>Landscape</span>
              <input list="landscape-list" value={form.landscape ?? ""} onChange={(e) => set("landscape", e.target.value)} />
              <datalist id="landscape-list">{landscapes.map((l) => <option key={l} value={l} />)}</datalist>
              <small>Groups systems, e.g. a business unit or customer.</small></label>
            <label className="field"><span>Product</span>
              <select value={form.product} onChange={(e) => changeProduct(e.target.value)}>
                {catalogue?.products.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
              </select></label>
            <label className="field"><span>Stack</span>
              <select value={form.stack} onChange={(e) => set("stack", e.target.value)}>
                {stacks.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
              </select></label>
            <label className="field"><span>Database</span>
              <select value={form.database} onChange={(e) => set("database", e.target.value)}>
                {databases.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
              </select></label>
            <label className="field"><span>Environment</span>
              <select value={form.environment} onChange={(e) => set("environment", e.target.value)}>
                {catalogue?.environments.map((e) => <option key={e.key} value={e.key}>{e.label}</option>)}
              </select></label>
            <label className="field"><span>License model</span>
              <select value={form.license_model ?? ""} onChange={(e) => set("license_model", e.target.value || null)}>
                <option value="">Product default{licenseDefault ? ` (${licenseDefault.label})` : ""}</option>
                {catalogue?.license_models.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
              </select></label>
            <label className="field"><span>Product version</span>
              <input value={form.product_version ?? ""} onChange={(e) => set("product_version", e.target.value)} placeholder="2023 FPS02 / EHP8" /></label>
            <label className="field"><span>SAP_BASIS / NW release</span>
              <input value={form.basis_release ?? ""} onChange={(e) => set("basis_release", e.target.value)} placeholder="758 / 7.50" /></label>
            <label className="field"><span>Host</span>
              <input value={form.host ?? ""} onChange={(e) => set("host", e.target.value)} placeholder="s4prd.corp.example.com" /></label>
            <label className="field"><span>Default client</span>
              <input maxLength={3} value={form.default_client ?? ""} onChange={(e) => set("default_client", e.target.value)} placeholder="100" /></label>
            <label className="field"><span>System timezone</span>
              <input list="tz-list" value={form.timezone ?? ""} onChange={(e) => set("timezone", e.target.value)} placeholder="Asia/Kolkata" />
              <datalist id="tz-list">{TIMEZONES.map((z) => <option key={z} value={z} />)}</datalist>
              <small>Time zone SAP reports job, lock and import times in (default UTC).</small></label>
            <label className="field wide"><span>Description</span>
              <textarea rows={2} value={form.description ?? ""} onChange={(e) => set("description", e.target.value)} /></label>
            {can("admin") && (
              <label className="field"><span>Start / stop from Sentinel</span>
                <select value={form.control_enabled === null || form.control_enabled === undefined ? "" : form.control_enabled ? "on" : "off"}
                        onChange={(e) => set("control_enabled", e.target.value === "" ? null : e.target.value === "on")}>
                  <option value="">Default (allowed except PRD)</option><option value="on">Allowed</option><option value="off">Not allowed</option>
                </select></label>
            )}
            <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <input type="checkbox" checked={!!form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
              <span>Monitoring enabled</span></label>
          </div>
        </div>
        <div className="dialog-foot">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={saving}>{saving ? "Saving…" : initial ? "Save changes" : "Register system"}</button>
        </div>
      </form>
    </dialog>
  );
}
