import { Check, Clock, Download, OctagonX, RefreshCw, TriangleAlert } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { RequireRole } from "../components/session";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../components/ui/dialog";
import { Card, Empty, ErrorBanner, PageHeader, Tile, fmtDate, useAsync } from "../components/ui";
import { Skeleton } from "../components/ui/skeleton";
import { cn } from "../lib/utils";
import type { CheckStatus, DailyCheckCell, DailyChecks as Matrix } from "../types";

const STATUS: Record<CheckStatus, { label: string; icon: typeof Check; cls: string }> = {
  critical: { label: "Critical", icon: OctagonX, cls: "bg-bad/15 text-bad" },
  warning: { label: "Warning", icon: TriangleAlert, cls: "bg-warn/15 text-warn" },
  stale: { label: "Stale", icon: Clock, cls: "bg-muted text-muted-foreground" },
  ok: { label: "OK", icon: Check, cls: "bg-good/10 text-good" },
  no_data: { label: "No data", icon: Clock, cls: "text-muted-foreground" },
};

type Fmt = Matrix["checks"][number]["format"];
const show = (f: Fmt, v: number) =>
  f === "flag" ? (v >= 1 ? "Up" : "Down")
  : `${fmt(v)}${{ pct: "%", ms: " ms", hours: " h", minutes: " min", days: " d", count: "" }[f] ?? ""}`;
const fmt = (v: number) => (Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toLocaleString(undefined, { maximumFractionDigits: 1 }));

function threshold(c: DailyCheckCell) {
  const t = c.threshold;
  const parts = [t.warning != null && `warning ${t.operator} ${t.warning}`, t.critical != null && `critical ${t.operator} ${t.critical}`].filter(Boolean);
  return `${parts.join(", ")} (${t.source === "rule" ? "alert rule" : "default"})`;
}

function Cell({ cell, label, tcode, format }: { cell?: DailyCheckCell; label: string; tcode: string; format: Fmt }) {
  if (!cell) return <td className="px-1 py-1.5 text-center text-xs text-muted-foreground/50">–</td>;
  const s = STATUS[cell.status];
  const Icon = s.icon;
  return (
    <td className="px-1 py-1.5 text-center">
      <span title={`${label}: ${show(format, cell.value)}\n${s.label}${cell.status === "stale" ? " (last value " + fmtDate(cell.at) + ")" : ""}\nThreshold: ${threshold(cell)}\nCheck in ${tcode}`}
        className={cn("inline-flex min-w-[3.25rem] items-center justify-center gap-1 whitespace-nowrap rounded-lg px-1.5 py-1 text-xs font-medium tabular-nums", s.cls)}>
        <Icon className="h-3 w-3 shrink-0" aria-label={s.label} />{show(format, cell.value)}
      </span>
    </td>
  );
}

export default function DailyChecks() {
  const [landscape, setLandscape] = useState("");
  const [environment, setEnvironment] = useState("");
  const [tick, setTick] = useState(0);
  const landscapes = useAsync(() => api.landscapes(), []);
  const { data, error, loading } = useAsync<Matrix>(() => api.dailyChecks({ landscape, environment }), [landscape, environment, tick]);
  const reviews = useAsync(() => api.dailyCheckReviews(), [tick]);
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const areas = useMemo(() => {
    const out: { area: string; checks: Matrix["checks"] }[] = [];
    for (const c of data?.checks ?? []) {
      const last = out[out.length - 1];
      if (last && last.area === c.area) last.checks.push(c);
      else out.push({ area: c.area, checks: [c] });
    }
    return out;
  }, [data]);
  const today = new Date().toISOString().slice(0, 10);
  const reviewedToday = (reviews.data ?? []).find((r) => r.check_date === today && (r.landscape ?? "") === landscape);

  return (
    <>
      <PageHeader title="Daily checks" description="The Basis morning checklist for every system, with the thresholds of your alert rules. Hover a cell for the value, threshold and transaction."
        actions={<>
          <select aria-label="Landscape" className="filter-select !w-auto" value={landscape} onChange={(e) => setLandscape(e.target.value)}>
            <option value="">All landscapes</option>
            {(landscapes.data ?? []).map((l) => <option key={l.landscape} value={l.landscape}>{l.landscape}</option>)}
          </select>
          <select aria-label="Environment" className="filter-select !w-auto" value={environment} onChange={(e) => setEnvironment(e.target.value)}>
            <option value="">All environments</option>
            {["PRD", "PRE", "QAS", "DEV", "SBX", "TRN"].map((e) => <option key={e} value={e}>{e}</option>)}
          </select>
          <Button variant="outline" onClick={() => setTick((t) => t + 1)}><RefreshCw className={cn(loading && "animate-spin")} />Refresh</Button>
          <Button variant="outline" onClick={() => api.downloadDailyChecks({ landscape, environment }).catch((e) => setMessage({ ok: false, text: e.message }))}><Download />CSV</Button>
          <RequireRole role="operator"><Button onClick={() => setOpen(true)}><Check />Sign off</Button></RequireRole>
        </>} />
      <ErrorBanner error={error} />
      {message && <div className={`banner ${message.ok ? "ok" : "error"}`}>{message.text}</div>}
      <div className="tiles !grid-cols-2 lg:!grid-cols-5">
        <Tile label="Systems" value={data?.summary.systems ?? "–"} hint={data ? `checked ${fmtDate(data.generated_at)}` : undefined} />
        <Tile label="Critical" value={data?.summary.critical ?? "–"} tone={data?.summary.critical ? "bad" : undefined} hint="systems with a critical check" />
        <Tile label="Warning" value={data?.summary.warning ?? "–"} tone={data?.summary.warning ? "warn" : undefined} hint="systems with a warning" />
        <Tile label="Stale data" value={data?.summary.stale ?? "–"} hint="systems with outdated values" />
        <Tile label="Today's sign-off" value={reviewedToday ? "Done" : "Open"} tone={reviewedToday ? "good" : undefined}
          hint={reviewedToday ? `${reviewedToday.reviewed_by}, ${fmtDate(reviewedToday.reviewed_at)}` : "not reviewed yet"} />
      </div>

      <Card title="Checklist">
        {!data ? <Skeleton className="h-64" /> : !data.systems.length ? <Empty>No systems match these filters.</Empty> : (
          <div className="overflow-x-auto rounded-2xl bg-surface">
            <table className="min-w-full border-separate border-spacing-0 text-sm">
              <thead>
                <tr>
                  <th rowSpan={2} className="sticky left-0 z-10 bg-surface px-3 py-2 text-left align-bottom">System</th>
                  {areas.map((a) => <th key={a.area} colSpan={a.checks.length} className="border-b border-border/60 px-2 pb-1 pt-2 text-center text-xs font-semibold">{a.area}</th>)}
                </tr>
                <tr>
                  {areas.map((a) => a.checks.map((c) => (
                    <th key={c.key} className="px-1 pb-2 pt-1 align-bottom text-[11px] font-medium leading-tight text-muted-foreground" style={{ minWidth: 76 }}>
                      <div>{c.label}</div><div className="font-mono text-[10px] opacity-70">{c.tcode}</div>
                    </th>
                  )))}
                </tr>
              </thead>
              <tbody>
                {data.systems.map((s) => (
                  <tr key={s.id} className="border-t border-border/60 hover:bg-muted/40">
                    <td className="sticky left-0 z-10 bg-surface px-3 py-1.5">
                      <div className="flex items-center gap-2">
                        <span className={cn("inline-flex h-5 w-5 items-center justify-center rounded-md", STATUS[s.overall].cls)} title={STATUS[s.overall].label}>
                          {(() => { const I = STATUS[s.overall].icon; return <I className="h-3 w-3" aria-label={STATUS[s.overall].label} />; })()}
                        </span>
                        <Link to={`/systems/${s.id}/basis`} className="font-mono font-semibold">{s.sid}</Link>
                        <span className="text-xs text-muted-foreground">{s.environment}</span>
                      </div>
                    </td>
                    {areas.map((a) => (
                      <Fragment key={a.area}>{a.checks.map((c) => <Cell key={c.key} cell={s.cells[c.key]} label={c.label} tcode={c.tcode} format={c.format} />)}</Fragment>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 text-xs text-muted-foreground">Empty cells: the signal is not collected for that system (for example HANA checks on Oracle, or security checks without the ABAP push agent). Stale: the newest value is older than the check expects.</p>
      </Card>

      <Card title="Sign-off history">
        {reviews.data?.length ? (
          <ul className="divide-y divide-border/60">
            {reviews.data.slice(0, 14).map((r) => (
              <li key={r.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2 text-sm">
                <span className="font-medium">{r.check_date}</span>
                <span className="text-muted-foreground">{r.landscape ?? "All landscapes"}</span>
                <span>{r.reviewed_by}, {fmtDate(r.reviewed_at)}</span>
                <span className="text-xs text-muted-foreground">{r.summary.critical} critical, {r.summary.warning} warning of {r.summary.systems}</span>
                {r.comment && <span className="w-full text-muted-foreground">{r.comment}</span>}
              </li>
            ))}
          </ul>
        ) : <Empty>No sign-offs yet. Review the checklist and sign it off; the results at that moment are kept.</Empty>}
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Sign off today's checks</DialogTitle>
            <DialogDescription>
              Records that you reviewed {landscape || "all landscapes"} now, with the current results
              ({data?.summary.critical ?? 0} critical, {data?.summary.warning ?? 0} warning).
            </DialogDescription>
          </DialogHeader>
          <label className="field"><span>Comment (actions taken, open points)</span>
            <textarea rows={4} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="e.g. RV_BELEG range extended in SNRO; SM58 entries for PI_PROD restarted" />
          </label>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button onClick={async () => {
              try {
                await api.reviewDailyChecks({ landscape: landscape || null, comment: comment || null });
                setMessage({ ok: true, text: "Checks signed off" });
                setComment("");
                setOpen(false);
                setTick((t) => t + 1);
              } catch (e) { setMessage({ ok: false, text: (e as Error).message }); }
            }}>Sign off</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
