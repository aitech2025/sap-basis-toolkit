import { useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { api } from "../../api";
import { DatabaseCard, HostCard, LayerStack } from "../../components/layers";
import { ALL_LAYERS } from "../../components/MonitoringLayers";
import { RequireRole } from "../../components/session";
import { Card, Empty, ErrorBanner, Pill, fmtDate, useAsync } from "../../components/ui";
import { Button } from "../../components/ui/button";
import { Skeleton } from "../../components/ui/skeleton";
import { cn } from "../../lib/utils";
import type { DatabaseLayer, Infrastructure, OsInfo, SystemDetail } from "../../types";

export default function InfrastructureTab({ system }: { system: SystemDetail }) {
  const { data, error, setData, loading } = useAsync(() => api.infrastructure(system.id), [system.id]);
  const [busy, setBusy] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  const refresh = async () => {
    setBusy(true);
    setRefreshError(null);
    try { setData(await api.refreshInfrastructure(system.id)); } catch (e) { setRefreshError((e as Error).message); } finally { setBusy(false); }
  };
  if (!data) return loading ? <Skeleton className="h-96" /> : <ErrorBanner error={error} />;
  const inf: Infrastructure = data;
  const app = inf.application?.attributes as { product?: string; product_version?: string; stack?: string; basis_release?: string } | undefined;
  const db = inf.database?.attributes as Partial<DatabaseLayer> & { db_sid?: string } | undefined;
  const dbHostAsset = inf.hosts.find((h) => h.attributes.role === "database");
  const appHosts = inf.hosts.filter((h) => h !== dbHostAsset);
  const failed = inf.refresh?.filter((r) => r.status !== "ok") ?? [];

  if (!inf.hosts.length && !inf.database) {
    return (
      <Empty>
        <div className="font-semibold text-foreground">No infrastructure recorded for {system.sid} yet</div>
        <div className="mt-1">Run <Link to="/discover">Discover system</Link> with an OS login (root or {system.sid.toLowerCase()}adm) on an application
          server. Sentinel then records the database server and the operating system of each host.</div>
      </Empty>
    );
  }

  return (
    <>
      <ErrorBanner error={refreshError || (failed.length ? failed.map((f) => f.error).join("; ") : null)} />
      <Card title="Application, database and operating system"
            description="Recorded by discovery and refreshed by the OS inventory collectors. Links show which layer runs on which server."
            actions={<RequireRole role="operator"><Button variant="outline" onClick={refresh} disabled={busy || !inf.collectors.length}>
              <RefreshCw className={cn(busy && "animate-spin")} />{busy ? "Refreshing…" : "Refresh inventory"}</Button></RequireRole>}>
        <LayerStack
          off={ALL_LAYERS.filter((l) => !system.monitoring_layers.includes(l))}
          application={
            <div className="rounded-2xl bg-surface p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-lg font-bold">{system.sid}</span>
                <span className="rounded-full bg-accent px-2.5 py-0.5 text-xs font-semibold text-accent-foreground">{system.product_label}{system.product_version ? ` ${system.product_version}` : ""}</span>
                <span className="text-xs text-muted-foreground">{app?.stack ?? system.stack}{app?.basis_release ? `, SAP_BASIS ${app.basis_release}` : ""}</span>
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {inf.instances.filter((i) => i.role !== "HANA").map((i) => (
                  <span key={i.id} className="inline-flex items-center gap-1.5 rounded-full bg-card px-2.5 py-1 font-mono text-xs">
                    <span className={cn("h-1.5 w-1.5 rounded-full", i.status === "GREEN" ? "bg-good" : i.status === "YELLOW" ? "bg-warn" : i.status === "RED" ? "bg-bad" : "bg-idle")} />
                    <strong>{i.role}</strong> {i.instance_name ?? i.instance_nr} · {i.hostname}
                  </span>
                ))}
              </div>
              <Link to={`/systems/${system.id}/instances`} className="mt-3 inline-block text-xs font-semibold">Instances &amp; control</Link>
            </div>
          }
          database={<DatabaseCard db={db ? { ...db, on_application_host: !dbHostAsset && !!db.host && appHosts.some((h) => h.hostname === db.host) } : null} fallbackType={system.database} />}
          hosts={
            <div className="grid gap-3 xl:grid-cols-2">
              {[...appHosts, ...(dbHostAsset ? [dbHostAsset] : [])].map((h) => (
                <HostCard key={h.id} host={h.hostname ?? h.name} os={h.attributes as OsInfo} hostKey={h.attributes.ssh_host_key}
                          role={h.attributes.role} />
              ))}
            </div>
          }
        />
      </Card>
      <Card title="OS inventory collectors" description="Read-only SSH. The host key is pinned; a changed key stops collection until an admin trusts it.">
        {inf.collectors.length ? (
          <div className="divide-y divide-border/60 overflow-hidden rounded-2xl bg-surface">
            {inf.collectors.map((c) => (
              <div key={c.id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
                <Pill value={c.last_status ?? "pending"} />
                <span className="font-mono font-semibold">{c.username}@{c.host}:{c.port}</span>
                <span className="text-xs text-muted-foreground">every {Math.round(c.interval_seconds / 60)} min, last run {fmtDate(c.last_run_at)}</span>
                {c.last_error && <span className="w-full text-xs text-bad">{c.last_error}</span>}
              </div>
            ))}
          </div>
        ) : <Empty>No OS inventory collector. Add one under Setup &amp; export guide, or re-run discovery with an OS login.</Empty>}
      </Card>
    </>
  );
}
