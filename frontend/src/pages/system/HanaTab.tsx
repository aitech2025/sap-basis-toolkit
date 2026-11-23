import { MonitorCard, NoData, Trend, useMonitor } from "../../components/monitor";
import { LayerOffNotice } from "../../components/MonitoringLayers";
import { Card, DataTable, ErrorBanner, Pill, Tile, fmtDate, fmtNumber } from "../../components/ui";
import type { SystemDetail } from "../../types";

type Hana = {
  version: string | null; cpu_pct: number | null;
  memory: { used: number; limit: number; pct: number } | null; disk: { used: number; total: number; pct: number } | null;
  alerts: { id: number; rating: number; details: string; at: string | null }[];
  backups: Record<string, { at: string | null; age_h: number | null }>;
  replication: { host: string; port: number; secondary: string; mode: string; status: string; details: string }[];
  services: { host: string; service: string; active: string }[];
  active_statements?: { count: number; long_running: number; max_duration_s: number };
  service_threads?: { count: number; waiting: number };
  expensive_statements_1h?: { count: number; max_duration_s: number };
};
const gb = (b: number | undefined) => (b == null ? "–" : `${fmtNumber(b / 1024 ** 3, 0)} GB`);
const RATING: Record<number, string> = { 1: "info", 2: "low", 3: "medium", 4: "high", 5: "error" };

export default function HanaTab({ system }: { system: SystemDetail }) {
  const mon = useMonitor(system, "hana");
  const h = mon.data?.latest?.data as Hana | undefined;
  const layerOff = !system.monitoring_layers.includes("database") && <LayerOffNotice sid={system.sid} layer="database" />;
  if (mon.data && !h) return <>{layerOff}<Card title="SAP HANA"><NoData system={system} what="HANA" hint="Add a HANA SQL collector with a read-only monitoring user." /></Card></>;
  const data = h?.backups["complete data backup"] ?? h?.backups["data snapshot"];
  const log = h?.backups["log backup"];
  return (
    <>
      {layerOff}
      <ErrorBanner error={mon.error} />
      {h && (
        <div className="tiles">
          <Tile label="Memory used" value={h.memory ? `${h.memory.pct}%` : "–"} hint={h.memory ? `${gb(h.memory.used)} of ${gb(h.memory.limit)}` : undefined} />
          <Tile label="CPU" value={h.cpu_pct != null ? `${h.cpu_pct}%` : "–"} />
          <Tile label="Data volume used" value={h.disk ? `${h.disk.pct}%` : "–"} hint={h.disk ? `${gb(h.disk.used)} of ${gb(h.disk.total)}` : undefined} />
          <Tile label="Last data backup" value={data?.age_h != null ? `${data.age_h} h ago` : "none"} hint={fmtDate(data?.at)} />
          <Tile label="Last log backup" value={log?.age_h != null ? `${Math.round(log.age_h * 60)} min ago` : "none"} hint={fmtDate(log?.at)} />
          <Tile label="Active statements" value={h.active_statements?.count ?? "n/a"} hint={h.active_statements ? `${h.active_statements.long_running} running ≥30s` : "View or privilege unavailable"} />
          <Tile label="Waiting threads" value={h.service_threads?.waiting ?? "n/a"} hint={h.service_threads ? `${h.service_threads.count} total service threads` : "View or privilege unavailable"} />
          <Tile label="Expensive statements (1h)" value={h.expensive_statements_1h?.count ?? "n/a"} hint={h.expensive_statements_1h ? `Max duration ${h.expensive_statements_1h.max_duration_s}s` : "Trace/view may be disabled"} />
        </div>
      )}
      <div className="cols">
        <MonitorCard title="Memory used (%)" detail={mon.data}>
          <Trend points={mon.data?.series["hana.memory_used_pct"]} unit="%" label="HANA memory used" threshold={{ value: 85, label: "85% warning" }} />
        </MonitorCard>
        <Card title="CPU (%)"><Trend points={mon.data?.series["hana.cpu_pct"]} unit="%" label="HANA CPU" threshold={{ value: 80, label: "80% warning" }} /></Card>
      </div>
      {h && (
        <>
          <Card title="Statistics server alerts">
            <DataTable rows={h.alerts} rowKey={(a, i) => `${a.id}-${i}`} empty="No current alerts." columns={[
              { key: "rating", label: "Rating", render: (a) => <Pill value={a.rating >= 5 ? "critical" : a.rating >= 3 ? "warning" : "info"} /> },
              { key: "r", label: "", render: (a) => RATING[a.rating] ?? a.rating }, { key: "id", label: "Check", numeric: true },
              { key: "details", label: "Details" }, { key: "at", label: "Since", render: (a) => fmtDate(a.at) }]} />
          </Card>
          <div className="cols">
            <Card title="System replication">
              <DataTable rows={h.replication} rowKey={(r) => `${r.host}-${r.port}`} empty="System replication is not configured." columns={[
                { key: "status", label: "Status", render: (r) => <Pill value={r.status === "ACTIVE" ? "healthy" : "critical"} /> },
                { key: "host", label: "Primary" }, { key: "secondary", label: "Secondary" }, { key: "mode", label: "Mode" }, { key: "port", label: "Port", numeric: true }]} />
            </Card>
            <Card title={`Services${h.version ? ` · HANA ${h.version}` : ""}`}>
              <DataTable rows={h.services} rowKey={(s) => `${s.host}-${s.service}`} columns={[
                { key: "active", label: "", render: (s) => <Pill value={s.active === "YES" ? "healthy" : "critical"} /> },
                { key: "service", label: "Service" }, { key: "host", label: "Host" }]} />
            </Card>
          </div>
        </>
      )}
    </>
  );
}
