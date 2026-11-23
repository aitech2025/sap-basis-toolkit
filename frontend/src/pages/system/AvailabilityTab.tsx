import { Link } from "react-router-dom";
import { Freshness, NoData, Trend, useMonitor } from "../../components/monitor";
import { LayerOffNotice } from "../../components/MonitoringLayers";
import { Card, DataTable, ErrorBanner, Pill, Tile, fmtNumber } from "../../components/ui";
import type { SystemDetail } from "../../types";

type Instance = { name: string; hostname: string; instance_nr: string; features: string; status: string; error: string | null;
  processes: { name: string; description: string; status: string; textstatus: string; elapsed: string; pid: string }[] };
type WP = { no: string; type: string; status: string; reason: string; err: number; time_s: number; program: string; client: string; user: string; action: string; table: string; instance?: string };

const statusPill = (s: string) => <Pill value={s === "green" ? "running" : s === "yellow" ? "degraded" : s === "red" ? "failed" : "stopped"} />;
const dur = (s: number) => (s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min` : s >= 60 ? `${Math.round(s / 60)} min` : `${s}s`);

export default function AvailabilityTab({ system }: { system: SystemDetail }) {
  const avail = useMonitor(system, "availability");
  const wp = useMonitor(system, "workprocesses");
  const a = avail.data?.latest?.data as { summary: { instances: number; running: number; degraded: number; down: number; system_up: boolean }; instances: Instance[] } | undefined;
  const w = wp.data?.latest?.data as { summary: { by_type: Record<string, { total: number; busy: number; stopped: number; busy_pct: number }>; long_running: number; stopped: number; restarts: number; threshold_s: number }; long_running: WP[]; stopped: WP[] } | undefined;

  const layerOff = !system.monitoring_layers.includes("application") && <LayerOffNotice sid={system.sid} layer="application" />;
  if (avail.data && !avail.data.latest) {
    return <>{layerOff}<Card title="Availability"><NoData system={system} what="availability" hint="Availability and work processes come from the SAP Start Service (sapcontrol)." /></Card></>;
  }
  return (
    <>
      {layerOff}
      <ErrorBanner error={avail.error || wp.error} />
      {a && (
        <div className={`banner ${a.summary.system_up ? (a.summary.down || a.summary.degraded ? "info" : "ok") : "error"}`}>
          <strong>{system.sid} is {a.summary.system_up ? "available" : "NOT available"}</strong> — {a.summary.running} of {a.summary.instances} instances running
          {a.summary.degraded ? `, ${a.summary.degraded} degraded` : ""}{a.summary.down ? `, ${a.summary.down} down` : ""}. <Freshness detail={avail.data} />
        </div>
      )}
      <div className="tiles">
        {w && Object.entries(w.summary.by_type).map(([t, v]) => (
          <Tile key={t} label={`${t} work processes`} value={`${v.busy_pct}%`} hint={`${v.busy}/${v.total} busy${v.stopped ? ` · ${v.stopped} stopped` : ""}`} />
        ))}
        {w && <Tile label="Long-running" value={w.summary.long_running} hint={`> ${dur(w.summary.threshold_s)}`} />}
        {w && <Tile label="WP restarts" value={w.summary.restarts} hint="since instance start" />}
      </div>
      <div className="cols">
        <Card title="Dialog work processes busy (%)">
          <Trend points={wp.data?.series["wp.dia_busy_pct"]} unit="%" label="DIA busy percentage" threshold={{ value: 80, label: "80% warning" }} />
        </Card>
        <Card title="Background work processes busy (%)">
          <Trend points={wp.data?.series["wp.btc_busy_pct"]} unit="%" label="BTC busy percentage" threshold={{ value: 90, label: "90% warning" }} />
        </Card>
      </div>
      <Card title="Instances & processes">
        <div className="instance-grid">
          {a?.instances.map((i) => (
            <div key={i.name} className="instance">
              <div className="instance-head"><strong>{i.hostname}</strong> <span className="muted">#{i.instance_nr}</span> {statusPill(i.status)}</div>
              <div className="muted small">{i.features.replaceAll("|", " · ")}</div>
              {i.error && <div className="text-bad small">{i.error}</div>}
              <ul className="mini-list">
                {i.processes.map((p) => <li key={p.name}>{statusPill(p.status)} {p.description || p.name} <span className="muted small">{p.textstatus}{p.elapsed ? ` · up ${p.elapsed}` : ""}</span></li>)}
              </ul>
            </div>
          ))}
        </div>
      </Card>
      <Card title={<>Long-running work processes <Freshness detail={wp.data} /></>}>
        <DataTable<WP> rows={w?.long_running ?? []} rowKey={(x) => `${x.instance}-${x.no}`} empty="No work process is running longer than the threshold."
          columns={[
            { key: "instance", label: "Instance" }, { key: "no", label: "WP" }, { key: "type", label: "Type" },
            { key: "time_s", label: "Running", numeric: true, render: (x) => dur(x.time_s) },
            { key: "program", label: "Program" }, { key: "user", label: "User" }, { key: "client", label: "Client" },
            { key: "action", label: "Action" }, { key: "table", label: "Table" },
          ]} />
        {!!w?.stopped.length && (
          <>
            <h3 style={{ margin: "16px 0 8px" }}>Stopped / ended work processes</h3>
            <DataTable<WP> rows={w.stopped} rowKey={(x) => `${x.instance}-s${x.no}`}
              columns={[{ key: "instance", label: "Instance" }, { key: "no", label: "WP" }, { key: "type", label: "Type" },
                        { key: "status", label: "Status" }, { key: "reason", label: "Reason" }, { key: "err", label: "Errors", numeric: true }]} />
          </>
        )}
        <p className="muted small">Analyse in SM50/SM66. Data: sapcontrol ABAPGetWPTable. {fmtNumber(w?.summary.restarts ?? 0, 0)} restarts recorded. <Link to={`/systems/${system.id}/setup`}>Collector settings</Link></p>
      </Card>
    </>
  );
}
