import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Activity, HardDrive, Link2, RefreshCw, Server, Sparkles } from "lucide-react";
import { Card, DataTable, Empty, ErrorBanner, PageHeader, Tile } from "../components/ui";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "../components/ui/dialog";
import { Skeleton } from "../components/ui/skeleton";
import { cn } from "../lib/utils";
import type { AnomaliesResponse, AvailabilityResponse, CapacityForecastResponse, IncidentCorrelationResponse, RootCauseResponse } from "../types/intelligence";

const WINDOWS = [{ label: "24 hours", value: 24 }, { label: "7 days", value: 168 }, { label: "30 days", value: 720 }];
const fmt = (value: number | null | undefined, suffix = "") => value == null ? "–" : `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}${suffix}`;
const date = (value: string | null | undefined) => value ? new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "Not observed";

const SEVERITY: Record<string, "bad" | "warn" | "idle" | "good"> = { critical: "bad", warning: "warn", high: "good", medium: "warn", low: "idle" };

function ForecastValue({ label, data }: { label: string; data: CapacityForecastResponse["systems"][number]["forecasts"]["disk"] }) {
  const state = data.status === "threshold_projected" ? "text-warn" : data.status === "insufficient_data" ? "text-muted-foreground" : "text-foreground";
  return (
    <div className="min-w-0 space-y-0.5 text-xs text-muted-foreground">
      <div>{label}</div>
      <div className={cn("text-sm font-semibold", state)}>
        {data.status === "insufficient_data" ? "Not enough history" : data.status === "stable_or_declining" ? "Stable or declining" : data.status === "threshold_projected" ? "Threshold approaching" : "Growing"}
      </div>
      {data.projected_utilisation_pct != null && <div>{fmt(data.projected_utilisation_pct, "%")} in {data.horizon_days} days</div>}
      {data.threshold_in_days != null && <div>{data.threshold_in_days === 0 ? "Threshold already reached" : `85% in about ${data.threshold_in_days} days`}</div>}
      {data.daily_change_mb != null && <div>{fmt(data.daily_change_mb, " MB/day")}</div>}
      {data.reason && <div>{data.reason}</div>}
    </div>
  );
}

function Section({ title, note, method, children }: { title: string; note?: string; method: string; children: React.ReactNode }) {
  return (
    <Card title={title} description={method} actions={note && <span className="text-xs text-muted-foreground">{note}</span>}>
      {children}
    </Card>
  );
}

export default function Intelligence() {
  const [hours, setHours] = useState(24);
  const [availability, setAvailability] = useState<AvailabilityResponse | null>(null);
  const [anomalies, setAnomalies] = useState<AnomaliesResponse | null>(null);
  const [capacity, setCapacity] = useState<CapacityForecastResponse | null>(null);
  const [correlations, setCorrelations] = useState<IncidentCorrelationResponse | null>(null);
  const [rootCause, setRootCause] = useState<RootCauseResponse | null>(null);
  const [rootCauseLoading, setRootCauseLoading] = useState(false);
  const [rootCauseError, setRootCauseError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([
      api.intelligenceAvailability(hours),
      api.intelligenceAnomalies(Math.min(hours, 24)),
      api.intelligenceCapacity(30, 85),
      api.intelligenceCorrelations(hours),
    ]).then(([a, n, c, i]) => {
      if (!active) return;
      setAvailability(a); setAnomalies(n); setCapacity(c); setCorrelations(i);
    }).catch((e) => active && setError(e instanceof Error ? e.message : "Unable to load operational intelligence"))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [hours, refreshKey]);

  const totalSamples = availability?.systems.reduce((n, s) => n + s.samples, 0) ?? 0;
  const anomaliesList = anomalies?.anomalies ?? [];
  const forecastCount = capacity?.systems.length ?? 0;
  const thresholdCount = capacity?.systems.reduce((n, s) => n + Number(s.forecasts.disk.status === "threshold_projected" || s.forecasts.memory.status === "threshold_projected"), 0) ?? 0;
  const correlationCount = correlations?.incidents.length ?? 0;

  const runRootCause = async (incidentId: number) => {
    setRootCauseLoading(true); setRootCauseError(null); setRootCause(null);
    try { setRootCause(await api.intelligenceRootCause(incidentId)); }
    catch (e) { setRootCauseError(e instanceof Error ? e.message : "Root cause analysis failed"); }
    finally { setRootCauseLoading(false); }
  };

  const windowLabel = WINDOWS.find((w) => w.value === hours)?.label;
  return (
    <>
      <PageHeader title="Intelligence" description="Explainable signals computed from recorded telemetry: observed availability, anomalies, capacity outlook and incident context."
        actions={<>
          <select aria-label="Analysis window" className="filter-select !w-auto" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            {WINDOWS.map((w) => <option key={w.value} value={w.value}>Last {w.label}</option>)}
          </select>
          <Button variant="outline" disabled={loading} onClick={() => setRefreshKey((v) => v + 1)}><RefreshCw className={cn(loading && "animate-spin")} />Refresh</Button>
        </>} />
      <ErrorBanner error={error} />
      <div className="tiles !grid-cols-2 lg:!grid-cols-4">
        <Tile label="Systems observed" icon={Server} value={`${availability?.systems.filter((s) => s.samples > 0).length ?? "–"}/${availability?.systems.length ?? "–"}`} hint={`${totalSamples.toLocaleString()} availability samples`} />
        <Tile label="Anomaly signals" icon={Activity} value={anomaliesList.length} tone={anomaliesList.some((a) => a.severity === "critical") ? "bad" : anomaliesList.length ? "warn" : undefined} hint="Deviations from the 7-day baseline" />
        <Tile label="Capacity outlook" icon={HardDrive} value={forecastCount} tone={thresholdCount ? "warn" : undefined} hint={`${thresholdCount} system${thresholdCount === 1 ? "" : "s"} heading for 85%`} />
        <Tile label="Incidents with context" icon={Link2} value={correlationCount} hint="Metric changes near the incident" />
      </div>

      <div className="grid gap-4 xl:grid-cols-2 [&>.card]:mb-0">
        <Section title="Observed availability" note={`Last ${windowLabel}`}
          method="Share of collected samples in which the system was up. Not a wall-clock SLA: periods without collection are excluded, and maintenance overlaps are listed but not subtracted.">
          {availability?.systems.length ? (
            <DataTable rows={availability.systems} rowKey={(r) => r.system_id} columns={[
              { key: "sid", label: "System", render: (r) => <><Link to={`/systems/${r.system_id}`} className="font-mono font-semibold">{r.sid}</Link><div className="text-xs text-muted-foreground">{r.landscape}</div></> },
              { key: "pct", label: "Availability", numeric: true, render: (r) => r.observed_availability_pct == null ? <span className="text-muted-foreground">No data</span>
                : <span className={cn("font-semibold", r.observed_availability_pct < 99 ? "text-warn" : "")}>{fmt(r.observed_availability_pct, "%")}</span> },
              { key: "samples", label: "Up / down", numeric: true, render: (r) => `${r.up_samples} / ${r.down_samples}` },
              { key: "maint", label: "Maint.", numeric: true, render: (r) => r.maintenance_windows_overlapping_period },
              { key: "last", label: "Last sample", render: (r) => <span className="text-xs">{date(r.last_observed_at)}</span> },
            ]} />
          ) : <Empty>No availability samples yet. They appear once a sapcontrol collector has run.</Empty>}
        </Section>

        <Section title="Anomalies" note={`7-day baseline, last ${Math.min(hours, 24)} h`}
          method="Median/MAD robust score. A signal needs at least 12 baseline samples and a score of 3.5 or more. Treat signals as leads to investigate, not as incidents.">
          {anomaliesList.length ? (
            <DataTable rows={anomaliesList} rowKey={(a) => `${a.system_id}-${a.metric}`} columns={[
              { key: "sev", label: "Severity", render: (a) => <Badge variant={SEVERITY[a.severity] ?? "idle"} className="capitalize">{a.severity}</Badge> },
              { key: "sid", label: "System", render: (a) => <Link to={`/systems/${a.system_id}`} className="font-mono font-semibold">{a.sid}</Link> },
              { key: "metric", label: "Metric", render: (a) => <code className="text-xs">{a.metric}</code> },
              { key: "latest", label: "Latest", numeric: true, render: (a) => <><span className="font-semibold">{fmt(a.latest_value)}</span><div className="text-xs text-muted-foreground">median {fmt(a.baseline_median)}</div></> },
              { key: "z", label: "Score", numeric: true, render: (a) => <span className={a.direction === "high" ? "text-bad" : "text-primary"}>{a.robust_z_score > 0 ? "+" : ""}{a.robust_z_score}</span> },
            ]} />
          ) : <Empty>{loading ? "Evaluating telemetry…" : "No anomalies. Signals need enough baseline history and a clear deviation."}</Empty>}
        </Section>

        <Section title="Capacity outlook" note="90-day history, 85% threshold"
          method="Linear trend from recorded capacity snapshots; needs at least three snapshots over time. Projections are directional estimates, not exhaustion dates.">
          {capacity?.systems.length ? (
            <div className="divide-y divide-border/60 overflow-hidden rounded-2xl bg-surface">
              {capacity.systems.map((s) => (
                <div key={s.system_id} className="grid gap-3 p-3 sm:grid-cols-[minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,1fr)]">
                  <div className="min-w-0"><Link to={`/systems/${s.system_id}`} className="font-mono font-semibold">{s.sid}</Link>
                    <div className="truncate text-xs text-muted-foreground">{s.landscape}, {s.environment}</div></div>
                  <ForecastValue label="Disk" data={s.forecasts.disk} />
                  <ForecastValue label="Memory" data={s.forecasts.memory} />
                </div>
              ))}
            </div>
          ) : <Empty>No capacity snapshots yet. Forecasts appear after disk and memory snapshots have been recorded.</Empty>}
        </Section>

        <div className="xl:col-span-2">
        <Section title="Incident context" note="30 minutes either side of opening"
          method="Metrics that changed around the time an incident opened. Timing alone does not prove cause; run root-cause analysis for ranked hypotheses.">
          {correlations?.incidents.length ? (
            <div className="grid gap-3 lg:grid-cols-2">
              {correlations.incidents.slice(0, showAll ? undefined : 6).map((i) => (
                <div key={i.incident_id} className="rounded-2xl bg-surface p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={SEVERITY[i.severity] ?? "idle"} className="capitalize">{i.severity}</Badge>
                    <Link to={`/incidents/${i.incident_id}`} className="min-w-0 flex-1 truncate font-medium text-foreground">#{i.incident_id} {i.title}</Link>
                    <Button size="sm" variant="outline" disabled={rootCauseLoading || !i.system_id} onClick={() => void runRootCause(i.incident_id)}>
                      <Sparkles />Root cause
                    </Button>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">{i.sid ?? "Platform"}, opened {date(i.opened_at)}</div>
                  {i.related_metrics.length ? (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {i.related_metrics.slice(0, 6).map((m) => (
                        <span key={m.metric} className="rounded border bg-muted/50 px-2 py-0.5 text-xs">
                          <code>{m.metric}</code> <span className={cn("font-semibold", (m.change ?? 0) > 0 ? "text-bad" : "")}>{m.change == null ? "n/a" : `${m.change > 0 ? "+" : ""}${fmt(m.change)}`}</span>
                        </span>
                      ))}
                    </div>
                  ) : <p className="mt-2 text-xs text-muted-foreground">No metric samples in the window.</p>}
                </div>
              ))}
            </div>
          ) : <Empty>No incidents in this window.</Empty>}
          {(correlations?.incidents.length ?? 0) > 6 && (
            <Button variant="ghost" size="sm" className="mt-3" onClick={() => setShowAll((v) => !v)}>
              {showAll ? "Show fewer" : `Show all ${correlations!.incidents.length} incidents`}
            </Button>
          )}
        </Section>
        </div>
      </div>

      <Dialog open={rootCauseLoading || !!rootCause || !!rootCauseError} onOpenChange={(o) => { if (!o) { setRootCause(null); setRootCauseError(null); } }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Root-cause hypotheses{rootCause ? ` for incident #${rootCause.incident.id}${rootCause.incident.sid ? ` on ${rootCause.incident.sid}` : ""}` : ""}</DialogTitle>
            <DialogDescription>{rootCause?.interpretation ?? "Ranking contributing domains from metrics around the incident."}</DialogDescription>
          </DialogHeader>
          {rootCauseLoading && <div className="space-y-2"><Skeleton className="h-20" /><Skeleton className="h-20" /></div>}
          {rootCauseError && <div className="banner error">{rootCauseError}</div>}
          {rootCause && (
            <div className="space-y-3">
              {rootCause.hypotheses.length ? rootCause.hypotheses.map((h, idx) => (
                <article key={h.domain} className="rounded-2xl bg-card p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">{idx + 1}</span>
                    <strong className="flex-1">{h.title}</strong>
                    <Badge variant={SEVERITY[h.confidence] ?? "idle"} className="capitalize">{h.confidence} evidence</Badge>
                    <span className="text-sm font-semibold tabular-nums">{h.score}/10</span>
                  </div>
                  <p className="mt-2 text-sm text-muted-foreground">{h.reason}</p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {h.supporting_signals.map((sig) => (
                      <span key={sig.metric} className="rounded border bg-muted/50 px-2 py-0.5 text-xs"><code>{sig.metric}</code> {fmt(sig.latest_value)} / {fmt(sig.threshold)}</span>
                    ))}
                  </div>
                  <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">{h.recommended_checks.map((r) => <li key={r}>{r}</li>)}</ul>
                </article>
              )) : <Empty>No hypothesis could be ranked. Check telemetry coverage and collector health first.</Empty>}
              <p className="text-xs text-muted-foreground">
                Evidence: {rootCause.coverage.metrics_observed} metrics, {rootCause.coverage.monitor_snapshots_in_window ?? 0} monitor snapshots
                {rootCause.coverage.monitors_observed?.length ? ` (${rootCause.coverage.monitors_observed.join(", ")})` : ""}.
              </p>
              {rootCause.limitations.length > 0 && <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">{rootCause.limitations.map((l) => <li key={l}>{l}</li>)}</ul>}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
