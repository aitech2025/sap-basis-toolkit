import { Freshness, Trend, useMonitor } from "../../components/monitor";
import { Card, DataTable, Pill, Tile, fmtNumber } from "../../components/ui";
import type { SystemDetail } from "../../types";

type Node = { instance: string; path: string; name: string; color: string; description: string; value: number | null; time: string };
type Alert = { instance: string; object: string; attribute: string; color: string; description: string; time: string };
type Lock = { user_name: string; table_name: string; argument: string | null; age_minutes: number; client: string | null; transaction: string | null };
type Live = {
  summary: {
    queues: Record<string, { type: string; now: number; high: number; max: number }>;
    ccms_red: number; ccms_yellow: number; open_alerts: number; locks: number | null;
    enqueue: { table_used_pct: number | null; locks_now: number | null; locks_max: number | null } | null;
    dialog_response_ms: number | null; users_logged_on: number | null;
  };
  ccms_red: Node[]; ccms_yellow: Node[]; open_alerts: Alert[]; locks_longest: Lock[];
};

const colorPill = (c: string) => <Pill value={c === "RED" ? "critical" : c === "YELLOW" ? "warning" : "healthy"} />;

/** Near-real-time ABAP signals read through sapstartsrv: dispatcher queues, CCMS and the enqueue server. */
export default function AbapLiveSection({ system }: { system: SystemDetail }) {
  const live = useMonitor(system, "abap_live");
  const d = live.data?.latest?.data as Live | undefined;
  if (!d) return null;
  const s = d.summary;
  const dia = s.queues.DIA;
  const current = [...d.ccms_red, ...d.ccms_yellow];
  return (
    <>
      <Card title={<>Live ABAP <Freshness detail={live.data} /></>}>
        <div className="tiles">
          <Tile label="Dialog queue" value={dia ? fmtNumber(dia.now, 0) : "–"} hint={dia ? `peak ${fmtNumber(dia.high, 0)} since start` : "not reported"} />
          <Tile label="Dialog response" value={s.dialog_response_ms != null ? `${fmtNumber(s.dialog_response_ms, 0)} ms` : "–"} hint="CCMS, average of instances" />
          <Tile label="Users logged on" value={s.users_logged_on != null ? fmtNumber(s.users_logged_on, 0) : "–"} hint="CCMS, all instances" />
          <Tile label="Lock entries" value={s.locks != null ? fmtNumber(s.locks, 0) : "–"}
            hint={s.enqueue?.table_used_pct != null ? `lock table ${fmtNumber(s.enqueue.table_used_pct, 1)}% used` : undefined} />
          <Tile label="CCMS now" value={`${s.ccms_red} red`} hint={`${s.ccms_yellow} yellow`} />
        </div>
        <div className="cols">
          <div><h3>Dialog queue</h3><Trend points={live.data?.series["abap.queue_dia"]} unit="requests" label="Dialog requests waiting" threshold={{ value: 10, label: "10 warning" }} /></div>
          <div><h3>Dialog response time</h3><Trend points={live.data?.series["abap.dialog_response_ms"]} unit="ms" label="Dialog response time" threshold={{ value: 1500, label: "1500 ms warning" }} /></div>
        </div>
      </Card>
      <Card title="CCMS: currently red or yellow">
        <DataTable<Node> rows={current} rowKey={(n) => `${n.instance}-${n.path}`} empty="All instance CCMS values are green."
          columns={[
            { key: "color", label: "", render: (n) => colorPill(n.color) },
            { key: "instance", label: "Instance" },
            { key: "path", label: "Monitoring attribute", render: (n) => <code className="text-xs">{n.path.split("\\").slice(1).join(" › ")}</code> },
            { key: "description", label: "Value" }, { key: "time", label: "Since" },
          ]} />
        {!!d.open_alerts.length && (
          <details className="mt-3">
            <summary className="cursor-pointer text-sm text-muted-foreground">{d.open_alerts.length} open alerts in RZ20 (they stay open until completed there, so some may be old)</summary>
            <DataTable<Alert> rows={d.open_alerts} rowKey={(a) => `${a.instance}-${a.object}-${a.attribute}-${a.time}`}
              columns={[{ key: "color", label: "", render: (a) => colorPill(a.color) }, { key: "instance", label: "Instance" },
                        { key: "attribute", label: "Attribute" }, { key: "description", label: "Message" }, { key: "time", label: "Raised" }]} />
          </details>
        )}
        <p className="muted small">Read from each instance's shared memory through sapstartsrv (GetAlertTree, GetQueueStatistic, EnqGetStatistic, EnqGetLockTable), no RFC user needed.</p>
      </Card>
    </>
  );
}
