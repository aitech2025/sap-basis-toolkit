import type { ReactNode } from "react";
import { AppWindow, Cpu, Database, HardDrive, KeyRound, MemoryStick, Server } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DatabaseLayer, MonitoringLayer, OsInfo } from "@/types";
import { Pill } from "./ui";

/** Application → database → operating system, drawn as three stacked layers joined by connectors.
 *  Layers listed in `off` are drawn muted and labelled as not monitored. */
export function LayerStack({ application, database, hosts, off = [] }: {
  application: ReactNode; database: ReactNode; hosts: ReactNode; off?: MonitoringLayer[];
}) {
  const layers: [MonitoringLayer, string, typeof Server, ReactNode][] = [
    ["application", "Application layer", AppWindow, application], ["database", "Database layer", Database, database],
    ["os", "Operating system layer", Server, hosts],
  ];
  return (
    <ol className="m-0 list-none space-y-0 p-0">
      {layers.map(([key, title, Icon, body], i) => {
        const muted = off.includes(key);
        return (
          <li key={key} className="relative pl-14">
            {i < layers.length - 1 && <span aria-hidden="true" className="absolute bottom-0 left-[21px] top-12 w-0.5 bg-gradient-to-b from-primary/50 to-primary/10" />}
            <span className={cn("absolute left-0 top-1 flex h-11 w-11 items-center justify-center rounded-full",
              muted ? "border-2 border-dashed border-border bg-surface text-muted-foreground" : "bg-foreground text-background")}><Icon className="h-5 w-5" /></span>
            <div className="pb-6">
              <div className="mb-2 flex flex-wrap items-center gap-2 pt-3 text-sm font-bold">
                {title}
                {muted && <span className="tag !ml-0">not monitored</span>}
              </div>
              <div className={cn(muted && "opacity-60")}>{body}</div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Fact({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("m-0 break-words text-sm font-semibold", mono && "break-all font-mono text-[13px]")}>{value ?? "–"}</dd>
    </div>
  );
}

export function DatabaseCard({ db, fallbackType }: { db: Partial<DatabaseLayer> | null | undefined; fallbackType?: string }) {
  if (!db || !(db.type || fallbackType)) return <div className="rounded-2xl bg-surface p-4 text-sm text-muted-foreground">Database not detected.</div>;
  return (
    <div className="rounded-2xl bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-lg font-bold">{db.db_sid ?? "–"}</span>
        <span className="rounded-full bg-accent px-2.5 py-0.5 text-xs font-semibold text-accent-foreground">{db.type ?? fallbackType}</span>
        {db.status && <Pill value={db.status.toLowerCase() === "running" ? "running" : db.status.toLowerCase()} />}
        {db.on_application_host && <span className="text-xs text-muted-foreground">on the application host</span>}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Fact label="Host" value={db.host} mono />
        <Fact label="Version" value={db.version} mono />
        <Fact label={db.type === "HANA" ? "Tenant" : "Database"} value={db.tenant} mono />
        <Fact label={db.type === "HANA" ? "Instance / schema" : "Schema"} value={[db.instance_nr && `HDB${db.instance_nr}`, db.schema].filter(Boolean).join(" · ") || null} mono />
      </dl>
    </div>
  );
}

export function HostCard({ host, os, hostKey, role }: { host: string; os: Partial<OsInfo> | null | undefined; hostKey?: string | null; role?: string }) {
  if (!os) {
    return <div className="rounded-2xl bg-surface p-4 text-sm text-muted-foreground"><span className="font-mono font-semibold text-foreground">{host}</span> was not inventoried.</div>;
  }
  const worst = Math.max(0, ...(os.filesystems ?? []).map((f) => f.used_pct));
  return (
    <div className="rounded-2xl bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-mono text-base font-bold">{os.fqdn ?? host}</div>
          <div className="text-xs text-muted-foreground">{os.os_pretty ?? os.os_name ?? "Unknown OS"}{os.arch ? `, ${os.arch}` : ""}</div>
        </div>
        {role && <span className="rounded-full bg-accent px-2.5 py-0.5 text-xs font-semibold capitalize text-accent-foreground">{role}</span>}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-2 2xl:grid-cols-4">
        <Fact label="Kernel" value={os.kernel} mono />
        <Fact label="CPU" value={os.cpu_count ? <span className="inline-flex items-center gap-1"><Cpu className="h-3.5 w-3.5" />{os.cpu_count} vCPU</span> : null} />
        <Fact label="Memory" value={os.memory_gb ? <span className="inline-flex items-center gap-1"><MemoryStick className="h-3.5 w-3.5" />{os.memory_gb} GB{os.memory_used_pct != null ? `, ${Math.round(os.memory_used_pct)}% used` : ""}</span> : null} />
        <Fact label="Platform" value={os.virtualization && os.virtualization !== "none" ? os.virtualization : "Physical / not detected"} />
        <Fact label="IP" value={os.ip_addresses?.[0]} mono />
        <Fact label="Uptime" value={os.uptime_days != null ? `${os.uptime_days} days` : null} />
        <Fact label="Load (1 min)" value={os.load1} />
        <Fact label="Time zone" value={os.timezone} />
      </dl>
      {!!os.filesystems?.length && (
        <div className="mt-3 grid gap-2">
          {os.filesystems.map((f) => (
            <div key={f.mount} className="flex items-center gap-2 text-xs">
              <HardDrive className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="w-24 truncate font-mono">{f.mount}</span>
              <div className="h-1.5 flex-1 rounded-full bg-muted"><div className={cn("h-1.5 rounded-full", f.used_pct >= 90 ? "bg-bad" : f.used_pct >= 80 ? "bg-warn" : "bg-primary")} style={{ width: `${f.used_pct}%` }} /></div>
              <span className="w-28 shrink-0 text-right tabular-nums text-muted-foreground">{f.used_pct}% of {f.size_gb} GB</span>
            </div>
          ))}
        </div>
      )}
      {hostKey && (
        <div className="mt-3 flex items-center gap-1.5 truncate text-[11px] text-muted-foreground" title="SSH host key, pinned on first contact">
          <KeyRound className="h-3 w-3 shrink-0" /><span className="truncate font-mono">{hostKey}</span>
        </div>
      )}
      {worst >= 90 && <p className="mt-2 text-xs font-semibold text-bad">A file system is above 90%.</p>}
    </div>
  );
}
