import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, ChevronRight, Plus, RefreshCw, Route } from "lucide-react";
import { api } from "../api";
import { RequireRole } from "../components/session";
import { Empty, ErrorBanner, PageHeader, Pill, StatusDot, Tile, statusTone, useAsync } from "../components/ui";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import { cn } from "../lib/utils";
import type { OverviewSystem } from "../types";

/** Transport order: systems are laid out the way changes travel through the landscape. */
const ENV_ORDER = ["SBX", "DEV", "TRN", "QAS", "PRE", "PRD", "OTHER"];
const ENV_LABEL: Record<string, string> = {
  SBX: "Sandbox", DEV: "Development", TRN: "Training", QAS: "Quality", PRE: "Pre-production", PRD: "Production", OTHER: "Other",
};
const EDGE: Record<string, string> = { good: "bg-good", warn: "bg-warn", bad: "bg-bad", idle: "bg-idle/50" };

function HealthMeter({ systems }: { systems: OverviewSystem[] }) {
  const counts = { healthy: 0, warning: 0, critical: 0, unknown: 0 } as Record<string, number>;
  systems.forEach((s) => { counts[s.health] = (counts[s.health] ?? 0) + 1; });
  const total = systems.length || 1;
  const parts: [string, string, string][] = [["healthy", "bg-good", "Healthy"], ["warning", "bg-warn", "Warning"], ["critical", "bg-bad", "Critical"], ["unknown", "bg-idle/40", "No data"]];
  return (
    <div className="rounded-lg border bg-card p-4 shadow-[0_1px_2px_hsl(var(--shadow)/0.06)]">
      <div className="flex items-baseline justify-between">
        <div className="text-sm font-medium">Estate health</div>
        <div className="text-xs text-muted-foreground">{systems.length} systems</div>
      </div>
      <div className="mt-3 flex h-2.5 gap-0.5 overflow-hidden rounded-full bg-muted" role="img"
           aria-label={parts.map(([k, , l]) => `${counts[k]} ${l.toLowerCase()}`).join(", ")}>
        {parts.map(([k, cls]) => counts[k] > 0 && <span key={k} className={cls} style={{ width: `${(counts[k] / total) * 100}%` }} />)}
      </div>
      <dl className="mt-3 grid grid-cols-4 gap-2">
        {parts.map(([k, cls, label]) => (
          <div key={k}>
            <dt className="flex items-center gap-1.5 text-xs text-muted-foreground"><span className={cn("h-2 w-2 rounded-sm", cls)} />{label}</dt>
            <dd className="m-0 text-lg font-semibold tabular-nums">{counts[k]}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function SystemCell({ s }: { s: OverviewSystem }) {
  const tone = statusTone(s.health);
  const incidents = s.incidents?.total ?? 0;
  const disk = s.growth.disk_used_pct;
  const avail = s.availability;
  return (
    <Link to={`/systems/${s.id}`}
      className="group relative block overflow-hidden rounded-lg border bg-card p-3.5 pl-4 text-foreground shadow-[0_1px_2px_hsl(var(--shadow)/0.06)] transition-colors hover:border-primary/40 hover:no-underline focus-visible:border-primary">
      <span aria-hidden="true" className={cn("absolute inset-y-0 left-0 w-1", EDGE[tone])} />
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-mono text-xl font-semibold leading-none tracking-tight">{s.sid}</div>
          <div className="mt-1.5 truncate text-xs text-muted-foreground">{s.product_label} · {s.database}</div>
        </div>
        <Pill value={s.health} />
      </div>
      <dl className="mt-3.5 grid grid-cols-3 gap-2 text-xs">
        <div>
          <dt className="text-muted-foreground">Instances</dt>
          <dd className="m-0 mt-0.5 font-medium tabular-nums">
            {avail ? <span className={cn(avail.running < avail.instances && "text-warn")}>{avail.running}/{avail.instances}</span> : "–"}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Incidents</dt>
          <dd className={cn("m-0 mt-0.5 font-medium tabular-nums", s.incidents?.critical ? "text-bad" : incidents ? "text-warn" : "")}>{incidents || "–"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Disk</dt>
          <dd className="m-0 mt-0.5 font-medium tabular-nums">{disk == null ? "–" : `${Math.round(disk)}%`}</dd>
          {disk != null && (
            <div className="mt-1 h-1 rounded-full bg-muted">
              <div className={cn("h-1 rounded-full", disk >= 90 ? "bg-bad" : disk >= 80 ? "bg-warn" : "bg-primary/60")} style={{ width: `${Math.min(100, disk)}%` }} />
            </div>
          )}
        </div>
      </dl>
    </Link>
  );
}

function LandscapeLane({ name, systems }: { name: string; systems: OverviewSystem[] }) {
  const stages = ENV_ORDER.filter((env) => systems.some((s) => s.environment === env));
  const worst = systems.some((s) => s.health === "critical") ? "bad" : systems.some((s) => s.health === "warning") ? "warn" : "good";
  return (
    <section className="rounded-xl border bg-card/60 p-4 lg:p-5" aria-labelledby={`lane-${name}`}>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <StatusDot tone={worst} />
          <h2 id={`lane-${name}`} className="text-base">{name}</h2>
          <span className="text-xs text-muted-foreground">{systems.length} system{systems.length === 1 ? "" : "s"}</span>
        </div>
        <Button variant="ghost" size="sm" asChild>
          <Link to={`/landscapes/${encodeURIComponent(name)}`} className="hover:no-underline"><Route />Transport routes</Link>
        </Button>
      </div>
      <div className="grid gap-4 overflow-x-auto pb-1 md:gap-3 md:[grid-template-columns:var(--lane-cols)]"
           style={{ "--lane-cols": `repeat(${stages.length}, minmax(220px, 1fr))` } as React.CSSProperties}>
        {stages.map((env, i) => (
          <div key={env} className="min-w-0">
            <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <span>{ENV_LABEL[env] ?? env}</span>
              {i < stages.length - 1 && <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/50" aria-hidden="true" />}
            </div>
            <div className="grid gap-2.5">
              {systems.filter((s) => s.environment === env).map((s) => <SystemCell key={s.id} s={s} />)}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export default function OverviewPage() {
  const [landscape, setLandscape] = useState("");
  const landscapes = useAsync(() => api.landscapes(), []);
  const { data, error, loading, reload } = useAsync(() => api.overview(landscape || undefined), [landscape]);
  const systems = useMemo(() => data?.systems ?? [], [data]);
  const groups = useMemo(() => {
    const map = new Map<string, OverviewSystem[]>();
    for (const s of systems) map.set(s.landscape, [...(map.get(s.landscape) ?? []), s]);
    for (const list of map.values()) list.sort((a, b) => a.sid.localeCompare(b.sid));
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [systems]);
  const incidents = systems.reduce((n, s) => n + (s.incidents?.total ?? 0), 0);
  const critical = systems.reduce((n, s) => n + (s.incidents?.critical ?? 0), 0);
  const production = systems.filter((s) => s.environment === "PRD");
  const prodHealthy = production.filter((s) => s.health === "healthy").length;
  const instances = systems.reduce((a, s) => ({ up: a.up + (s.availability?.running ?? 0), all: a.all + (s.availability?.instances ?? 0) }), { up: 0, all: 0 });
  const certSoon = systems.filter((s) => s.certificates.soonest_expiry_days != null && s.certificates.soonest_expiry_days <= 30).length;

  return (
    <>
      <PageHeader
        title="Landscape overview"
        description="Every SAP system in transport order, with its health, running instances, open incidents and disk headroom."
        actions={<>
          <select aria-label="Landscape" className="filter-select !w-48" value={landscape} onChange={(e) => setLandscape(e.target.value)}>
            <option value="">All landscapes</option>
            {landscapes.data?.map((l) => <option key={l.landscape} value={l.landscape}>{l.landscape} ({l.systems})</option>)}
          </select>
          <Button variant="outline" onClick={reload} disabled={loading}><RefreshCw className={cn(loading && "animate-spin")} />Refresh</Button>
          <RequireRole role="admin"><Button asChild><Link to="/discover" className="hover:no-underline"><Plus />Add system</Link></Button></RequireRole>
        </>}
      />
      <ErrorBanner error={error} />

      {!data && loading && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-[92px]" />)}</div>
      )}

      {data && (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.4fr)] [&>*:last-child]:col-span-2 lg:[&>*:last-child]:col-span-1 [&>*:nth-child(3)]:col-span-2 sm:[&>*:nth-child(3)]:col-span-1">
            <Tile label="Production healthy" value={production.length ? `${prodHealthy}/${production.length}` : "–"}
                  tone={production.length && prodHealthy < production.length ? "bad" : production.length ? "good" : undefined}
                  hint={production.length ? `${production.length - prodHealthy} need review` : "No production systems"} />
            <Tile label="Open incidents" value={incidents} tone={critical ? "bad" : incidents ? "warn" : "good"}
                  hint={<Link to="/incidents">{critical ? `${critical} critical` : "View queue"}</Link>} />
            <Tile label="Instances running" value={instances.all ? `${instances.up}/${instances.all}` : "–"}
                  tone={instances.all && instances.up < instances.all ? "warn" : instances.all ? "good" : undefined}
                  hint={instances.all ? `${instances.all - instances.up} not running` : "No availability data"} />
            <HealthMeter systems={systems} />
          </div>
          {certSoon > 0 && (
            <div className="banner warn"><span>{certSoon} system{certSoon === 1 ? " has" : "s have"} a certificate expiring within 30 days.</span>
              <Link to="/certificates" className="ml-auto inline-flex items-center gap-1 font-medium">Review<ArrowRight className="h-3.5 w-3.5" /></Link></div>
          )}

          {!systems.length && !loading ? (
            <Empty>No SAP systems yet. <Link to="/discover">Discover a system</Link> from its host to start monitoring.</Empty>
          ) : (
            <div className="space-y-4">
              {groups.map(([name, list]) => <LandscapeLane key={name} name={name} systems={list} />)}
            </div>
          )}
          <p className="mt-6 text-xs text-muted-foreground">Health reflects the latest collection. Systems without data are shown as “no data”, never as healthy.</p>
        </>
      )}
    </>
  );
}
