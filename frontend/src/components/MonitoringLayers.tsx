import { AppWindow, Database, Server, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { MonitoringLayer } from "@/types";

export const ALL_LAYERS: MonitoringLayer[] = ["application", "database", "os"];

/** label: headings and cards; name: mid-sentence; short: chips. */
export const LAYER_INFO: Record<MonitoringLayer, { label: string; name: string; short: string; icon: LucideIcon; covers: string }> = {
  application: {
    label: "SAP application", name: "SAP application", short: "Application", icon: AppWindow,
    covers: "Instance availability, work processes and system log through the SAP Start Service. With the ABAP schema on HANA, also short dumps, update errors, RFC queues, IDocs and background jobs.",
  },
  database: {
    label: "Database", name: "database", short: "Database", icon: Database,
    covers: "HANA memory, CPU, data volume, backups, alerts and replication; HANA instance availability; the database process on the database server.",
  },
  os: {
    label: "Operating system", name: "operating system", short: "OS", icon: Server,
    covers: "Memory, load and file systems of the application and database servers, over SSH or node_exporter.",
  },
};

/** Keep the canonical order whatever order the layers were toggled in. */
export const sortLayers = (layers: MonitoringLayer[]) => ALL_LAYERS.filter((l) => layers.includes(l));

/** "the SAP application and the database" */
export const layerNames = (layers: MonitoringLayer[]) => sortLayers(layers).map((l) => `the ${LAYER_INFO[l].name}`).join(" and ");

export function layerSummary(layers: MonitoringLayer[]): string {
  if (layers.length === ALL_LAYERS.length) return "Full monitoring: SAP application, database and operating system.";
  const off = ALL_LAYERS.filter((l) => !layers.includes(l));
  return `Monitoring ${layerNames(layers)} only; ${layerNames(off)} ${off.length > 1 ? "are" : "is"} not monitored.`;
}

/** Multi-select of the layers to monitor. At least one layer stays selected. */
export function LayerPicker({ value, onChange, className }: {
  value: MonitoringLayer[]; onChange: (layers: MonitoringLayer[]) => void; className?: string;
}) {
  const full = value.length === ALL_LAYERS.length;
  const toggle = (layer: MonitoringLayer, on: boolean) => onChange(sortLayers(on ? [...value, layer] : value.filter((l) => l !== layer)));
  return (
    <fieldset className={cn("m-0 min-w-0 border-0 p-0", className)}>
      <legend className="mb-2 p-0 text-[13px] font-medium">Monitoring layers</legend>
      <div className="grid gap-2.5 sm:grid-cols-3">
        {ALL_LAYERS.map((layer) => {
          const { label, icon: Icon, covers } = LAYER_INFO[layer];
          const on = value.includes(layer);
          const last = on && value.length === 1;
          return (
            <label key={layer} title={last ? "At least one layer must stay selected" : undefined}
              className={cn("relative flex min-w-0 cursor-pointer flex-col gap-2 rounded-2xl border-2 p-3.5 transition-colors",
                on ? "border-primary bg-accent" : "border-border bg-surface hover:border-primary/40",
                last && "cursor-not-allowed")}>
              <span className="flex items-center gap-2.5">
                <span className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
                  on ? "bg-foreground text-background" : "bg-muted text-muted-foreground")}>
                  <Icon className="h-4 w-4" aria-hidden="true" />
                </span>
                <span className="min-w-0 flex-1 text-sm font-semibold leading-tight">{label}</span>
                <input type="checkbox" className="h-4 w-4 shrink-0 accent-[hsl(var(--primary))]" checked={on} disabled={last}
                       onChange={(e) => toggle(layer, e.target.checked)} />
              </span>
              <span className="text-xs font-normal leading-snug text-muted-foreground">{covers}</span>
            </label>
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="m-0 text-xs font-normal text-muted-foreground" aria-live="polite">{layerSummary(value)}</p>
        {!full && (
          <button type="button" className="text-xs font-semibold text-primary hover:underline" onClick={() => onChange([...ALL_LAYERS])}>
            Monitor all layers
          </button>
        )}
      </div>
    </fieldset>
  );
}

/** Chips for the monitored layers (a missing chip means the layer is not monitored). */
export function LayerBadges({ layers, className }: { layers: MonitoringLayer[]; className?: string }) {
  const full = layers.length === ALL_LAYERS.length;
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-1", className)} title={layerSummary(layers)}
          aria-label={layerSummary(layers)}>
      {sortLayers(layers).map((layer) => {
        const { short, icon: Icon } = LAYER_INFO[layer];
        return (
          <span key={layer} aria-hidden="true"
                className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold",
                  full ? "bg-surface text-muted-foreground" : "bg-accent text-accent-foreground")}>
            <Icon className="h-3 w-3" />{short}
          </span>
        );
      })}
    </span>
  );
}

/** Shown on a tab whose layer is not monitored: older data stays visible, nothing new arrives. */
export function LayerOffNotice({ sid, layer }: { sid: string; layer: MonitoringLayer }) {
  return (
    <div className="banner info" role="status">
      <span><strong>{LAYER_INFO[layer].label} monitoring is turned off for {sid}.</strong> Its collectors are paused and raise no
        alerts. Data collected earlier is still shown. Choose <em>Edit</em> above to turn the layer back on.</span>
    </div>
  );
}
