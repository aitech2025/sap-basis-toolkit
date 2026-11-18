import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { LineChart } from "../components/LineChart";
import { Clock, Database, Gauge, RefreshCw, Server } from "lucide-react";
import { Card, Empty, ErrorBanner, PageHeader, Tile } from "../components/ui";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import { cn } from "../lib/utils";
import type { SapSystem } from "../types";
import type { AnalyticsMetric, AnalyticsSeries, AnalyticsSummary } from "../types/analytics";
import type { IncidentCorrelationResponse } from "../types/intelligence";

const RANGES = [{ label: "Last 6 hours", hours: 6 }, { label: "Last 24 hours", hours: 24 }, { label: "Last 7 days", hours: 168 }, { label: "Last 30 days", hours: 720 }];
const BUCKETS = [{ label: "1 minute", seconds: 60 }, { label: "5 minutes", seconds: 300 }, { label: "15 minutes", seconds: 900 }, { label: "30 minutes", seconds: 1800 }, { label: "1 hour", seconds: 3600 }, { label: "6 hours", seconds: 21600 }, { label: "1 day", seconds: 86400 }];

function valueFor(metric: AnalyticsMetric, value: number): number {
  return metric.key === "availability.system_up" ? value * 100 : value;
}
function formatValue(metric: AnalyticsMetric, value: number): string {
  const shown = valueFor(metric, value);
  return `${shown.toLocaleString(undefined, { maximumFractionDigits: 1 })}${metric.unit === "%" ? "%" : ` ${metric.unit}`}`;
}
function ageLabel(value: string | null): string {
  if (!value) return "No telemetry received";
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} hr ago`;
  return `${Math.floor(hours / 24)} days ago`;
}

export default function Analytics() {
  const [metrics, setMetrics] = useState<AnalyticsMetric[]>([]);
  const [systems, setSystems] = useState<SapSystem[]>([]);
  const [metricKey, setMetricKey] = useState("hana.cpu_pct");
  const [hours, setHours] = useState(24);
  const [bucketSeconds, setBucketSeconds] = useState(300);
  const [aggregation, setAggregation] = useState<"avg" | "min" | "max" | "last">("avg");
  const [landscape, setLandscape] = useState("");
  const [systemId, setSystemId] = useState("");
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [result, setResult] = useState<AnalyticsSeries | null>(null);
  const [correlations, setCorrelations] = useState<IncidentCorrelationResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let active = true;
    Promise.all([api.analyticsMetrics(), api.systems()]).then(([catalogue, registry]) => {
      if (!active) return;
      setMetrics(catalogue);
      setSystems(registry);
      if (!catalogue.some((m) => m.key === metricKey)) setMetricKey(catalogue[0]?.key ?? "");
    }).catch((e) => active && setError(e instanceof Error ? e.message : "Unable to load analytics catalogue"));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([
      api.analyticsSummary(hours, landscape || undefined),
      api.intelligenceCorrelations(hours, landscape || undefined),
      metricKey ? api.analyticsSeries(metricKey, {
        hours, bucketSeconds, aggregation,
        landscape: landscape || undefined,
        systemId: systemId ? Number(systemId) : undefined,
      }) : Promise.resolve(null),
    ]).then(([overview, incidents, series]) => {
      if (!active) return;
      setSummary(overview);
      setCorrelations(incidents);
      setResult(series);
    }).catch((e) => active && setError(e instanceof Error ? e.message : "Unable to load historical telemetry"))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [metricKey, hours, bucketSeconds, aggregation, landscape, systemId, refreshKey]);

  const visibleSystems = useMemo(() => systems.filter((s) => !landscape || s.landscape === landscape), [systems, landscape]);
  const groupedMetrics = useMemo(() => {
    const groups = new Map<string, AnalyticsMetric[]>();
    metrics.forEach((m) => groups.set(m.category, [...(groups.get(m.category) ?? []), m]));
    return [...groups.entries()];
  }, [metrics]);
  const selectedMetric = metrics.find((m) => m.key === metricKey);
  const series = result?.series ?? [];
  const pointsCount = series.reduce((n, s) => n + s.points.reduce((sum, p) => sum + p.samples, 0), 0);

  const rangeLabel = RANGES.find((r) => r.hours === hours)?.label;
  const bucketLabel = BUCKETS.find((b) => b.seconds === bucketSeconds)?.label;
  return (
    <>
      <PageHeader title="Analytics" description="Explore how SAP and HANA metrics developed over time, per system."
        actions={<>
          <select aria-label="Landscape" className="filter-select !w-auto" value={landscape} onChange={(e) => { setLandscape(e.target.value); setSystemId(""); }}>
            <option value="">All landscapes</option>
            {[...new Set(systems.map((s) => s.landscape))].sort().map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
          <select aria-label="Time range" className="filter-select !w-auto" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            {RANGES.map((r) => <option key={r.hours} value={r.hours}>{r.label}</option>)}
          </select>
          <Button variant="outline" onClick={() => setRefreshKey((v) => v + 1)} disabled={loading}><RefreshCw className={cn(loading && "animate-spin")} />Refresh</Button>
        </>} />
      <ErrorBanner error={error} />
      <div className="tiles !grid-cols-2 lg:!grid-cols-4">
        <Tile label="Systems reporting" icon={Server} value={summary ? `${summary.systems_reporting}/${summary.systems_total}` : "–"} hint={`With data in the ${rangeLabel?.toLowerCase()}`} />
        <Tile label="Observations" icon={Database} value={summary ? summary.samples.toLocaleString() : "–"} hint="All metrics, raw samples" />
        <Tile label="Metrics reporting" icon={Gauge} value={summary?.metrics_reporting ?? "–"} hint="Distinct metrics observed" />
        <Tile label="Latest observation" icon={Clock} value={<span className="text-xl">{ageLabel(summary?.last_sample_at ?? null)}</span>} hint="Most recent collector sample" />
      </div>

      <Card title="Metric" description={selectedMetric ? `${selectedMetric.description}. Values are aggregated per bucket; gaps stay empty and are never interpolated.` : undefined}
            actions={<span className="text-xs text-muted-foreground">{pointsCount.toLocaleString()} observations charted</span>}>
        <div className="form-grid">
          <label className="field"><span>Metric</span><select value={metricKey} onChange={(e) => setMetricKey(e.target.value)}>{groupedMetrics.map(([category, items]) => <optgroup key={category} label={category}>{items.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}</optgroup>)}</select></label>
          <label className="field"><span>System</span><select value={systemId} onChange={(e) => setSystemId(e.target.value)}><option value="">All systems</option>{visibleSystems.map((s) => <option key={s.id} value={s.id}>{s.sid} ({s.environment})</option>)}</select></label>
          <label className="field"><span>Aggregation</span><select value={aggregation} onChange={(e) => setAggregation(e.target.value as typeof aggregation)}><option value="avg">Average</option><option value="max">Maximum</option><option value="min">Minimum</option><option value="last">Latest in bucket</option></select></label>
          <label className="field"><span>Bucket size</span><select value={bucketSeconds} onChange={(e) => setBucketSeconds(Number(e.target.value))}>{BUCKETS.map((b) => <option key={b.seconds} value={b.seconds}>{b.label}</option>)}</select></label>
        </div>
      </Card>

      <div className="mb-3 mt-6 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base">{selectedMetric?.label ?? "Metric trend"}</h2>
        <span className="text-xs text-muted-foreground">{rangeLabel}, {bucketLabel} buckets</span>
      </div>
      {loading && !result ? <div className="grid gap-4 xl:grid-cols-2">{[0, 1].map((i) => <Skeleton key={i} className="h-72" />)}</div>
        : !series.length || !series.some((s) => s.points.length) ? (
          <Empty>
            <div className="font-medium text-foreground">No data for this selection</div>
            <div className="mt-1">Check that a collector is configured and ran during this period. Sentinel never invents sample data.</div>
            <Link to="/systems" className="mt-2 inline-block">Review system collectors</Link>
          </Empty>
        ) : (
          <div className="grid gap-4 xl:grid-cols-2">
            {series.filter((s) => s.points.length).map((s) => {
              const chartPoints = s.points.map((p) => ({ t: new Date(p.ts).getTime(), v: valueFor(selectedMetric!, p.value) }));
              const values = s.points.map((p) => valueFor(selectedMetric!, p.value));
              const latest = values[values.length - 1];
              const average = values.reduce((a, b) => a + b, 0) / values.length;
              const peak = Math.max(...values);
              return (
                <article key={s.system_id} className="rounded-3xl bg-card p-6">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <Link to={`/systems/${s.system_id}`} className="font-mono text-base font-semibold text-foreground">{s.sid}</Link>
                      <div className="truncate text-xs text-muted-foreground">{s.name}, {s.landscape}</div>
                    </div>
                    <Badge variant={s.environment === "PRD" ? "bad" : "secondary"} className="font-mono">{s.environment}</Badge>
                  </div>
                  <dl className="my-3 grid grid-cols-3 gap-2">
                    {[["Latest", latest], ["Average", average], ["Peak", peak]].map(([label, v]) => (
                      <div key={label as string} className="rounded-2xl bg-surface px-3 py-2"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="m-0 text-lg font-bold tabular-nums">{formatValue(selectedMetric!, v as number)}</dd></div>
                    ))}
                  </dl>
                  <LineChart points={chartPoints} unit={selectedMetric!.unit} label={`${s.sid} ${selectedMetric!.label}`} height={190}
                    markers={(correlations?.incidents ?? []).filter((i) => i.system_id === s.system_id && i.metric === metricKey).map((i) => ({ t: new Date(i.opened_at).getTime(), label: `#${i.incident_id} ${i.title}`, severity: i.severity }))} />
                  <div className="mt-2 flex justify-between text-xs text-muted-foreground"><span>{s.points.length} buckets</span><span>{s.points.reduce((n, p) => n + p.samples, 0)} raw samples</span></div>
                </article>
              );
            })}
          </div>
        )}
      {result?.truncated && <div className="banner info mt-4">This query hit the 100,000-observation limit. Narrow the time range or pick one system to see everything.</div>}
    </>
  );
}
