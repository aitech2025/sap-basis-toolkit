import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, Inbox, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "./badge";
import { Card as UICard, CardContent, CardDescription, CardHeader, CardTitle } from "./card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./table";

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

const STATUS_CLASS: Record<string, "good" | "warn" | "bad" | "idle"> = {
  healthy: "good", success: "good", pass: "good", sent: "good", info: "idle",
  warning: "warn", review: "warn", untrusted: "warn", pending: "idle", unknown: "idle", suppressed: "idle",
  critical: "bad", expired: "bad", error: "bad", fatal: "bad", block: "bad", fail: "bad", failed: "bad",
  unreachable: "bad", blocked: "warn", enabled: "good", disabled: "idle", recovered: "good",
  open: "bad", acknowledged: "warn", resolved: "good", active: "warn", planned: "idle", past: "idle", ok: "good", partial: "warn",
  running: "good", degraded: "warn", stopped: "bad", succeeded: "good", timeout: "bad", interrupted: "warn", aborted: "bad",
};

export function statusTone(value: string | null | undefined) {
  return STATUS_CLASS[value ?? "unknown"] ?? "idle";
}

const DOT: Record<string, string> = { good: "bg-good", warn: "bg-warn", bad: "bg-bad", idle: "bg-idle" };

export function StatusDot({ tone, pulse, className }: { tone: "good" | "warn" | "bad" | "idle"; pulse?: boolean; className?: string }) {
  return <span aria-hidden="true" className={cn("inline-block h-2 w-2 shrink-0 rounded-full", DOT[tone], pulse && "animate-pulse-ring", className)} />;
}

/** Status is always shown as a shape-coded dot plus text, never colour alone. */
export function Pill({ value }: { value: string | null | undefined }) {
  const v = value ?? "unknown";
  const tone = statusTone(v);
  return (
    <Badge variant={tone} className="pill capitalize">
      <span aria-hidden="true" className={cn("h-1.5 w-1.5", tone === "warn" ? "rotate-45 rounded-[1px]" : tone === "bad" ? "rounded-[1px]" : "rounded-full",
        tone === "idle" ? "border border-current" : "bg-current")} />
      {v === "unknown" ? "no data" : v.replace(/_/g, " ")}
    </Badge>
  );
}

/**
 * KPI card in the reference style: title, a dark round icon next to a large value, an optional delta,
 * and an optional mini chart on the right. `tone` adds a status dot to the title (never colour alone).
 */
export function Tile({ label, value, hint, tone, icon: Icon, chart, delta }: {
  label: string; value: ReactNode; hint?: ReactNode; tone?: "good" | "warn" | "bad";
  icon?: LucideIcon; chart?: ReactNode; delta?: { text: string; direction: "up" | "down"; good?: boolean };
}) {
  return (
    <div className="tile flex min-w-0 flex-col rounded-3xl bg-card p-5">
      <div className="flex items-center gap-2 text-[15px] font-semibold">
        {tone && <span aria-hidden="true" className={cn("h-2 w-2 rounded-full", DOT[tone])} />}
        <span className="truncate">{label}</span>
      </div>
      <div className="mt-auto flex items-center justify-between gap-3 pt-4">
        <div className="flex min-w-0 items-center gap-3">
          {Icon && <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-foreground text-background"><Icon className="h-5 w-5" /></span>}
          <span className="whitespace-nowrap text-[28px] font-bold leading-none tracking-tight tabular-nums">{value ?? "–"}</span>
          {delta && (
            <span className={cn("self-start whitespace-nowrap text-xs font-bold", delta.good ? "text-good" : "text-bad")} title={delta.text}>
              {delta.direction === "up" ? "↑" : "↓"}{delta.text.split(" ")[0]}
            </span>
          )}
        </div>
        {chart && <div className="hidden shrink-0 sm:block">{chart}</div>}
      </div>
      {hint && <div className="mt-2.5 truncate text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

export function ErrorBanner({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return (
    <div className="banner error" role="alert">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div>{error}</div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="empty flex flex-col items-center justify-center gap-2 rounded-2xl bg-surface px-6 py-10 text-center text-sm text-muted-foreground">
      <Inbox className="h-5 w-5" aria-hidden="true" />
      <div>{children}</div>
    </div>
  );
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
    <div className="table-wrap overflow-hidden rounded-2xl bg-surface">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            {columns.map((c) => <TableHead key={c.key} className={c.numeric ? "num text-right" : undefined}>{c.label}</TableHead>)}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow
              key={rowKey(row, i)}
              className={onRowClick ? "clickable cursor-pointer" : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              onClick={onRowClick ? (e) => {
                if ((e.target as HTMLElement).closest("a,button,input,select,textarea")) return;
                onRowClick(row);
              } : undefined}
              onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(row); } : undefined}
            >
              {columns.map((c) => (
                <TableCell key={c.key} className={c.numeric ? "num text-right tabular-nums" : undefined}>
                  {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? "–")}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function PageHeader({ title, description, actions, children }: {
  title: ReactNode; description?: ReactNode; actions?: ReactNode; children?: ReactNode;
}) {
  return (
    <div className="mb-7 flex flex-wrap items-center justify-between gap-4">
      <div className="min-w-0 space-y-1">
        <h1>{title}</h1>
        {description && <p className="max-w-3xl text-sm text-muted-foreground">{description}</p>}
        {children}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
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

export function Card({ title, description, actions, children, className }: {
  title?: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <UICard className={cn("card mb-4 min-w-0", className)}>
      {(title || actions) && (
        <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0">
          <div className="min-w-0 space-y-1">
            {title && <CardTitle>{title}</CardTitle>}
            {description && <CardDescription>{description}</CardDescription>}
          </div>
          {actions && <div className="actions">{actions}</div>}
        </CardHeader>
      )}
      <CardContent className={cn(!(title || actions) && "pt-6")}>{children}</CardContent>
    </UICard>
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
