import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

export function useAsync<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const reload = useCallback(() => {
    const id = ++seq.current;
    setLoading(true);
    load()
      .then((value) => {
        if (id === seq.current) {
          setData(value);
          setError(null);
        }
      })
      .catch((e: Error) => id === seq.current && setError(e.message))
      .finally(() => id === seq.current && setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    reload();
    const onKey = () => reload();
    window.addEventListener("apikey-changed", onKey);
    return () => window.removeEventListener("apikey-changed", onKey);
  }, [reload]);
  return { data, error, loading, reload, setData };
}

const STATUS_CLASS: Record<string, string> = {
  healthy: "good", success: "good", pass: "good", sent: "good", info: "neutral",
  warning: "warn", review: "warn", untrusted: "warn", pending: "neutral", unknown: "neutral", suppressed: "neutral",
  critical: "bad", expired: "bad", error: "bad", fatal: "bad", block: "bad", fail: "bad", failed: "bad",
  unreachable: "bad", blocked: "warn", enabled: "good", disabled: "neutral", recovered: "good",
  open: "bad", acknowledged: "warn", resolved: "good", active: "warn", planned: "neutral", past: "neutral", ok: "good", partial: "warn", running: "good", degraded: "warn", stopped: "bad",
  succeeded: "good", timeout: "bad", interrupted: "warn",
};
const STATUS_ICON: Record<string, string> = { good: "●", warn: "▲", bad: "■", neutral: "○" };

/** Status is always shown as icon + text, never colour alone. */
export function Pill({ value }: { value: string | null | undefined }) {
  const v = value ?? "unknown";
  const cls = STATUS_CLASS[v] ?? "neutral";
  return (
    <span className={`pill ${cls}`}>
      <span aria-hidden="true">{STATUS_ICON[cls]}</span> {v === "unknown" ? "no data" : v}
    </span>
  );
}

export function Tile({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value ?? "–"}</div>
      {hint && <div className="tile-hint">{hint}</div>}
    </div>
  );
}

export function ErrorBanner({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  const auth = /api key|locked/i.test(error);
  return (
    <div className="banner error" role="alert">
      {error}
      {auth && <> — set the key in <a href="/settings">Settings</a>.</>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export interface Column<T> {
  key: string;
  label: string;
  render?: (row: T) => ReactNode;
  numeric?: boolean;
}

export function DataTable<T>({ rows, columns, empty = "Nothing to show.", rowKey, onRowClick }: {
  rows: T[];
  columns: Column<T>[];
  empty?: ReactNode;
  rowKey: (row: T, index: number) => string | number;
  onRowClick?: (row: T) => void;
}) {
  if (!rows.length) return <Empty>{empty}</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((c) => <th key={c.key} className={c.numeric ? "num" : undefined}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={rowKey(row, i)}
              className={onRowClick ? "clickable" : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              onClick={onRowClick ? (e) => {
                if ((e.target as HTMLElement).closest("a,button,input,select,textarea")) return;
                onRowClick(row);
              } : undefined}
              onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(row); } : undefined}
            >
              {columns.map((c) => (
                <td key={c.key} className={c.numeric ? "num" : undefined}>
                  {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? "–")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FileField({ label, onText, accept, binary }: {
  label: string;
  accept?: string;
  binary?: boolean;
  onText: (content: string | ArrayBuffer | null, name: string | null) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="file"
        accept={accept}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          if (!file) return onText(null, null);
          onText(binary ? await file.arrayBuffer() : await file.text(), file.name);
        }}
      />
    </label>
  );
}

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-head">
          {title && <h3>{title}</h3>}
          {actions && <div className="actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function fmtNumber(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "–";
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function fmtDate(value: string | null | undefined): string {
  if (!value) return "–";
  const d = new Date(value.includes("T") || value.includes(" ") ? value.replace(" ", "T") : value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function fmtDay(value: string | null | undefined): string {
  if (!value) return "–";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function download(name: string, content: string, type = "text/csv") {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
