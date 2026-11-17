import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronRight, Cpu, Plus, RefreshCw, Server, ShieldCheck, Siren } from "lucide-react";
import { api } from "../api";
import { MiniBars, MultiTrend, RingProgress, SegmentGauge, Sparkline, StackedBars, type TimePoint } from "../components/charts";
import { RequireRole, useSession } from "../components/session";
import { Card, Empty, ErrorBanner, PageHeader, Pill, StatusDot, Tile, statusTone, useAsync } from "../components/ui";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import { cn } from "../lib/utils";
import type { Incident, OverviewSystem } from "../types";
import type { AnalyticsSeries } from "../types/analytics";

/** Transport order: systems are laid out the way changes travel through the landscape. */
const ENV_ORDER = ["SBX", "DEV", "TRN", "QAS", "PRE", "PRD", "OTHER"];
const ENV_LABEL: Record<string, string> = {
  SBX: "Sandbox", DEV: "Development", TRN: "Training", QAS: "Quality", PRE: "Pre-production", PRD: "Production", OTHER: "Other",
};
const TRENDS = [
  { key: "hana.memory_used_pct", label: "HANA memory", unit: "%" },
  { key: "wp.dia_busy_pct", label: "Dialog load", unit: "%" },
  { key: "dumps.last_hour", label: "Dumps / hour", unit: "dumps" },
] as const;
const DAY = 86400000;

/** Sum (or average) every system's bucket into one fleet-wide series. */
function fleet(series: AnalyticsSeries | null | undefined, mode: "sum" | "avg"): TimePoint[] {
  const acc = new Map<number, number[]>();
  series?.series.forEach((s) => s.points.forEach((p) => {
    const t = Date.parse(p.ts);
    acc.set(t, [...(acc.get(t) ?? []), p.value]);
  }));
  return [...acc.entries()].sort(([a], [b]) => a - b)
    .map(([t, vs]) => ({ t, v: mode === "sum" ? vs.reduce((a, b) => a + b, 0) : vs.reduce((a, b) => a + b, 0) / vs.length }));
}

function perDay(incidents: Incident[], days: number) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Array.from({ length: days }, (_, i) => {
    const start = today.getTime() - (days - 1 - i) * DAY;
    const inDay = incidents.filter((x) => { const t = Date.parse(x.opened_at); return t >= start && t < start + DAY; });
    const d = new Date(start);
    return {
      label: d.toLocaleDateString(undefined, { day: "2-digit", month: "short" }),
      short: d.toLocaleDateString(undefined, { weekday: "narrow" }),
      critical: inDay.filter((x) => x.severity === "critical").length,
      warning: inDay.filter((x) => x.severity !== "critical").length,
    };
  });
}

function SystemCell({ s }: { s: OverviewSystem }) {
  const tone = statusTone(s.health);
  const incidents = s.incidents?.total ?? 0;
  const disk = s.growth.disk_used_pct;
  const avail = s.availability;
  return (
    <Link to={`/systems/${s.id}`}
      className="group block rounded-2xl bg-surface p-4 text-foreground transition-shadow hover:no-underline hover:shadow-[0_12px_30px_-16px_hsl(var(--shadow)/0.45)] focus-visible:shadow-[0_0_0_2px_hsl(var(--ring))]">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-3">
          <span className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-full font-mono text-[11px] font-bold",
            tone === "bad" ? "bg-bad-soft text-bad" : tone === "warn" ? "bg-warn-soft text-warn" : tone === "good" ? "bg-good-soft text-good" : "bg-muted text-muted-foreground")}>
            {s.environment}
          </span>
          <div className="min-w-0">
            <div className="font-mono text-lg font-bold leading-none">{s.sid}</div>
            <div className="mt-1 truncate text-xs text-muted-foreground">{s.product_label}</div>
          </div>
        </div>
        <Pill value={s.health} />
      </div>
      <dl className="mt-4 grid grid-cols-3 gap-2 text-xs">
        <div><dt className="text-muted-foreground">Instances</dt>
          <dd className={cn("m-0 mt-0.5 text-sm font-bold tabular-nums", avail && avail.running < avail.instances && "text-warn")}>
            {avail ? `${avail.running}/${avail.instances}` : "–"}</dd></div>
        <div><dt className="text-muted-foreground">Incidents</dt>
          <dd className={cn("m-0 mt-0.5 text-sm font-bold tabular-nums", s.incidents?.critical ? "text-bad" : incidents ? "text-warn" : "")}>{incidents || "–"}</dd></div>
        <div><dt className="text-muted-foreground">Disk</dt>
          <dd className="m-0 mt-0.5 text-sm font-bold tabular-nums">{disk == null ? "–" : `${Math.round(disk)}%`}</dd>
          {disk != null && <div className="mt-1 h-1.5 rounded-full bg-muted"><div className={cn("h-1.5 rounded-full", disk >= 90 ? "bg-bad" : disk >= 80 ? "bg-warn" : "bg-primary")} style={{ width: `${Math.min(100, disk)}%` }} /></div>}
        </div>
      </dl>
    </Link>
  );
}

function LandscapeLane({ name, systems }: { name: string; systems: OverviewSystem[] }) {
  const stages = ENV_ORDER.filter((env) => systems.some((s) => s.environment === env));
  const worst = systems.some((s) => s.health === "critical") ? "bad" : systems.some((s) => s.health === "warning") ? "warn" : "good";
  return (
    <section className="rounded-3xl bg-card p-5 lg:p-6" aria-labelledby={`lane-${name}`}>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <StatusDot tone={worst} />
          <h2 id={`lane-${name}`} className="text-lg font-bold">{name}</h2>
          <span className="text-sm text-muted-foreground">{systems.length} system{systems.length === 1 ? "" : "s"}</span>
        </div>
        <Link to={`/landscapes/${encodeURIComponent(name)}`} className="inline-flex items-center gap-1 text-sm font-semibold text-muted-foreground hover:text-foreground hover:no-underline">
          Transport routes<ChevronRight className="h-4 w-4" />
        </Link>
      </div>
      <div className="grid gap-4 overflow-x-auto pb-1 md:gap-3 md:[grid-template-columns:var(--lane-cols)]"
           style={{ "--lane-cols": `repeat(${stages.length}, minmax(230px, 1fr))` } as React.CSSProperties}>
        {stages.map((env, i) => (
          <div key={env} className="min-w-0">
            <div className="mb-2 flex items-center gap-1.5 px-1 text-xs font-semibold text-muted-foreground">
              {ENV_LABEL[env] ?? env}{i < stages.length - 1 && <ChevronRight className="h-3.5 w-3.5 opacity-50" aria-hidden="true" />}
            </div>
            <div className="grid gap-2.5">{systems.filter((s) => s.environment === env).map((s) => <SystemCell key={s.id} s={s} />)}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

export default function OverviewPage() {
  const { me } = useSession();
  const [landscape, setLandscape] = useState("");
  const [trend, setTrend] = useState<(typeof TRENDS)[number]["key"]>("hana.memory_used_pct");
  const landscapes = useAsync(() => api.landscapes(), []);
  const { data, error, loading, reload } = useAsync(() => api.overview(landscape || undefined), [landscape]);
  const recent = useAsync(() => api.incidents("?status=all"), [landscape]);
  const down = useAsync(() => api.analyticsSeries("availability.instances_down", { hours: 24, bucketSeconds: 3600, aggregation: "max", landscape: landscape || undefined }), [landscape]);
  const dia = useAsync(() => api.analyticsSeries("wp.dia_busy_pct", { hours: 24, bucketSeconds: 3600, aggregation: "avg", landscape: landscape || undefined }), [landscape]);
  const trendData = useAsync(() => api.analyticsSeries(trend, { hours: 24, bucketSeconds: 1800, aggregation: "avg", landscape: landscape || undefined }), [trend, landscape]);

  const systems = useMemo(() => data?.systems ?? [], [data]);
  const ids = useMemo(() => new Set(systems.map((s) => s.id)), [systems]);
  const groups = useMemo(() => {
    const map = new Map<string, OverviewSystem[]>();
    for (const s of systems) map.set(s.landscape, [...(map.get(s.landscape) ?? []), s]);
    for (const list of map.values()) list.sort((a, b) => a.sid.localeCompare(b.sid));
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [systems]);
  const incidentsList = useMemo(() => (recent.data ?? []).filter((i) => !i.system_id || ids.has(i.system_id)), [recent.data, ids]);
  const days14 = useMemo(() => perDay(incidentsList, 14), [incidentsList]);
  const days7 = days14.slice(7);
  const thisWeek = days7.reduce((n, d) => n + d.critical + d.warning, 0);
  const lastWeek = days14.slice(0, 7).reduce((n, d) => n + d.critical + d.warning, 0);

  const open = systems.reduce((n, s) => n + (s.incidents?.total ?? 0), 0);
  const critical = systems.reduce((n, s) => n + (s.incidents?.critical ?? 0), 0);
  const production = systems.filter((s) => s.environment === "PRD");
  const prodHealthy = production.filter((s) => s.health === "healthy").length;
  const inst = systems.reduce((a, s) => ({ up: a.up + (s.availability?.running ?? 0), all: a.all + (s.availability?.instances ?? 0) }), { up: 0, all: 0 });
  const counts = { healthy: 0, warning: 0, critical: 0, unknown: 0 } as Record<string, number>;
  systems.forEach((s) => { counts[s.health] = (counts[s.health] ?? 0) + 1; });
  const healthyPct = systems.length ? Math.round((counts.healthy / systems.length) * 100) : 0;
  const diaFleet = fleet(dia.data, "avg");
  const downFleet = fleet(down.data, "sum");
  const trendMeta = TRENDS.find((t) => t.key === trend)!;
  const latestIncidents = [...incidentsList].sort((a, b) => Date.parse(b.opened_at) - Date.parse(a.opened_at)).slice(0, 4);
  const name = (me?.display_name || me?.username || "").split(" ")[0];

  return (
    <>
      <PageHeader
        title={`Welcome back${name ? `, ${name[0].toUpperCase()}${name.slice(1)}` : ""}!`}
        description={data ? (open ? `${open} open incident${open === 1 ? "" : "s"} across ${systems.length} systems${critical ? `, ${critical} critical` : ""}.` : `All ${systems.length} systems are quiet. No open incidents.`) : "Loading your SAP estate…"}
        actions={<>
          <select aria-label="Landscape" className="filter-select !h-11 !w-48 !rounded-full" value={landscape} onChange={(e) => setLandscape(e.target.value)}>
            <option value="">All landscapes</option>
            {landscapes.data?.map((l) => <option key={l.landscape} value={l.landscape}>{l.landscape} ({l.systems})</option>)}
          </select>
          <Button variant="outline" size="icon" onClick={reload} disabled={loading} aria-label="Refresh"><RefreshCw className={cn(loading && "animate-spin")} /></Button>
          <RequireRole role="admin"><Button size="lg" asChild><Link to="/discover" className="hover:no-underline"><Plus />Add system</Link></Button></RequireRole>
        </>}
      />
      <ErrorBanner error={error} />

      {!data && loading && <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-[136px]" />)}</div>}

      {data && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Tile label="Open incidents" icon={Siren} value={open} tone={critical ? "bad" : open ? "warn" : "good"}
                  delta={lastWeek || thisWeek ? { text: `${Math.abs(thisWeek - lastWeek)} ${thisWeek >= lastWeek ? "more" : "fewer"} than last week`, direction: thisWeek >= lastWeek ? "up" : "down", good: thisWeek <= lastWeek } : undefined}
                  hint={`${critical ? `${critical} critical, ` : ""}${thisWeek} opened in 7 days`}
                  chart={<MiniBars values={days7.map((d) => d.critical + d.warning)} labels={days7.map((d) => d.short)} />} />
            <Tile label="Production health" icon={ShieldCheck} value={production.length ? `${prodHealthy}/${production.length}` : "–"}
                  tone={production.length ? (prodHealthy === production.length ? "good" : "bad") : undefined}
                  hint={production.length ? `${production.length - prodHealthy} need review` : "No production systems"}
                  chart={<MiniBars values={production.slice(0, 6).map((s) => s.availability?.running ?? 0)} labels={production.slice(0, 6).map((s) => s.sid)} highlightLast={false} />} />
            <Tile label="Instances running" icon={Server} value={inst.all ? `${inst.up}/${inst.all}` : "–"}
                  tone={inst.all ? (inst.up === inst.all ? "good" : "warn") : undefined}
                  hint={downFleet.length ? "Instances down, last 24 h" : "No availability history yet"}
                  chart={<Sparkline points={downFleet} color="hsl(var(--warn))" />} />
            <Tile label="Dialog load" icon={Cpu} value={diaFleet.length ? `${Math.round(diaFleet[diaFleet.length - 1].v)}%` : "–"}
                  hint={diaFleet.length ? "Average DIA busy, last 24 h" : "No work process history yet"}
                  chart={<Sparkline points={diaFleet} />} />
          </div>

          <div className="mt-4 grid gap-4 xl:grid-cols-12">
            <Card title="Estate health" description="Latest health of every monitored system" className="mb-0 xl:col-span-3">
              <SegmentGauge size={210} total={systems.length || 1}
                segments={[{ value: counts.healthy, color: "hsl(var(--good))" }, { value: counts.warning, color: "hsl(var(--warn))" }, { value: counts.critical, color: "hsl(var(--bad))" }]}
                center={<>{healthyPct}<span className="text-2xl">%</span></>} caption="of systems healthy" />
              <dl className="mt-2 grid grid-cols-2 gap-2">
                {([["healthy", "bg-good", "Healthy"], ["warning", "bg-warn", "Warning"], ["critical", "bg-bad", "Critical"], ["unknown", "bg-idle/40", "No data"]] as const).map(([k, cls, label]) => (
                  <div key={k} className="rounded-2xl bg-surface px-3 py-2">
                    <dt className="flex items-center gap-1.5 text-xs text-muted-foreground"><span className={cn("h-2 w-2 rounded-full", cls)} />{label}</dt>
                    <dd className="m-0 text-lg font-bold tabular-nums">{counts[k]}</dd>
                  </div>
                ))}
              </dl>
            </Card>

            <Card title="Production watch" className="mb-0 xl:col-span-5"
                  actions={<Link to="/incidents" className="inline-flex items-center gap-1 text-sm font-semibold text-muted-foreground hover:text-foreground hover:no-underline">View incidents<ChevronRight className="h-4 w-4" /></Link>}>
              <div className="grid gap-3 sm:grid-cols-[1.15fr_1fr]">
                <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-[hsl(252_90%_72%)] via-primary to-[hsl(258_70%_50%)] p-5 text-white shadow-[0_18px_40px_-18px_hsl(var(--primary))]">
                  <div className="flex items-center justify-between text-sm font-semibold"><span>Production</span><span>{production.length} systems</span></div>
                  <div className="mt-4 space-y-2">
                    {production.length ? production.slice(0, 4).map((s) => (
                      <Link key={s.id} to={`/systems/${s.id}`} className="flex items-center justify-between gap-2 rounded-2xl bg-white/15 px-3 py-2 text-white backdrop-blur hover:bg-white/25 hover:no-underline">
                        <span className="font-mono text-sm font-bold">{s.sid}</span>
                        <span className="whitespace-nowrap text-xs opacity-90">{s.availability ? `${s.availability.running}/${s.availability.instances}` : "–"}</span>
                        <span className="flex items-center gap-1.5 whitespace-nowrap text-xs font-semibold capitalize">
                          <span className={cn("h-2 w-2 rounded-full", s.health === "healthy" ? "bg-emerald-300" : s.health === "critical" ? "bg-rose-300" : s.health === "warning" ? "bg-amber-300" : "bg-white/60")} />
                          {s.health === "unknown" ? "no data" : s.health}
                        </span>
                      </Link>
                    )) : <p className="text-sm opacity-90">No production systems registered.</p>}
                  </div>
                  <svg className="pointer-events-none absolute -bottom-10 -right-10 h-36 w-36 opacity-20" viewBox="0 0 100 100" aria-hidden="true">
                    <circle cx="50" cy="50" r="40" fill="none" stroke="white" strokeWidth="14" />
                  </svg>
                </div>
                <div className="rounded-3xl bg-surface p-4">
                  <div className="mb-3 text-sm font-bold">Latest incidents</div>
                  {latestIncidents.length ? (
                    <ol className="relative m-0 list-none space-y-3 border-l border-dashed border-border pl-4">
                      {latestIncidents.map((i) => (
                        <li key={i.id} className="relative text-xs">
                          <span className={cn("absolute -left-[21px] top-0.5 h-2.5 w-2.5 rounded-full ring-4 ring-surface", i.severity === "critical" ? "bg-bad" : "bg-warn")} />
                          <Link to={`/incidents/${i.id}`} className="line-clamp-2 font-semibold text-foreground">{i.title}</Link>
                          <div className="mt-0.5 text-muted-foreground">{i.sid ?? "Platform"}, {new Date(i.opened_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</div>
                        </li>
                      ))}
                    </ol>
                  ) : <p className="text-xs text-muted-foreground">Nothing has fired recently.</p>}
                </div>
              </div>
              <div className="mt-3 flex items-center gap-3 rounded-full bg-surface px-3 py-2">
                <RingProgress value={production.length ? prodHealthy / production.length : 0} />
                <span className="text-sm font-bold">{production.length ? Math.round((prodHealthy / production.length) * 100) : 0}% healthy</span>
                <span className="ml-auto truncate text-xs text-muted-foreground">{production.map((s) => s.sid).join(", ") || "No production systems"}</span>
              </div>
            </Card>

            <Card title="Trends, last 24 hours" className="mb-0 xl:col-span-4" description="One line per system, 30-minute averages">
              <div className="mb-3 inline-flex rounded-full bg-surface p-1" role="tablist" aria-label="Metric">
                {TRENDS.map((t) => (
                  <button key={t.key} role="tab" aria-selected={trend === t.key} onClick={() => setTrend(t.key)}
                    className={cn("rounded-full px-3 py-1.5 text-xs font-semibold transition-colors", trend === t.key ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground")}>
                    {t.label}
                  </button>
                ))}
              </div>
              <MultiTrend unit={trendMeta.unit} height={230}
                series={(trendData.data?.series ?? []).filter((s) => s.points.length).slice(0, 6).map((s) => ({
                  key: String(s.system_id), label: `${s.sid} (${s.environment})`, points: s.points.map((p) => ({ t: Date.parse(p.ts), v: p.value })),
                }))} />
            </Card>

            <Card title="Incidents opened, last 14 days" className="mb-0 xl:col-span-12"
                  actions={<span className="text-xs text-muted-foreground">{thisWeek} this week, {lastWeek} the week before</span>}>
              <StackedBars height={200} data={days14}
                keys={[{ key: "critical", label: "Critical", color: "hsl(var(--bad))" }, { key: "warning", label: "Warning", color: "hsl(var(--warn))" }]} />
            </Card>
          </div>

          <div className="mb-3 mt-8 flex items-baseline justify-between">
            <h2 className="text-xl font-bold">Landscapes</h2>
            <span className="text-xs text-muted-foreground">Systems in transport order</span>
          </div>
          {!systems.length && !loading ? (
            <Empty>No SAP systems yet. <Link to="/discover">Discover a system</Link> from its host to start monitoring.</Empty>
          ) : (
            <div className="space-y-4">{groups.map(([n, list]) => <LandscapeLane key={n} name={n} systems={list} />)}</div>
          )}
          <p className="mt-6 text-xs text-muted-foreground">Health reflects the latest collection. Systems without data are shown as “no data”, never as healthy.</p>
        </>
      )}
    </>
  );
}
