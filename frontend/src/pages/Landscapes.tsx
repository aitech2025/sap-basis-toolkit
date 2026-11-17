import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, ErrorBanner, Pill, useAsync } from "../components/ui";
import type { Topology } from "../types";

export default function Landscapes() {
  const { data, error, loading } = useAsync(async () => {
    const list = await api.landscapes();
    return Promise.all(list.map((l) => api.topology(l.landscape)));
  }, []);

  return (
    <>
      <div className="page-head">
        <div><h1>Landscapes</h1><p>Product tracks and their transport paths, DEV → QAS → PRD.</p></div>
        <Link to="/systems?new=1" className="btn primary">Add system</Link>
      </div>
      <ErrorBanner error={error} />
      {!loading && !data?.length && <Empty>No landscapes yet. Every system belongs to a landscape — <Link to="/systems?new=1">register one</Link>.</Empty>}
      <div className="system-grid">
        {data?.map((t: Topology) => {
          const systems = t.lanes.flatMap((l) => l.systems);
          const worst = ["critical", "warning", "healthy"].find((h) => systems.some((s) => s.health === h)) ?? "unknown";
          return (
            <Link key={t.landscape} to={`/landscapes/${encodeURIComponent(t.landscape)}`} className="system-card">
              <div className="top">
                <div><div className="sid">{t.landscape}</div><div className="muted">{systems.length} systems · {t.routes.length} routes</div></div>
                <Pill value={worst} />
              </div>
              <dl>
                {t.lanes.map((lane) => (
                  <div key={lane.key} style={{ display: "contents" }}>
                    <dt>{lane.product_label}</dt>
                    <dd style={{ flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
                      {lane.path ? lane.path.split(" | ").map((p) => <span key={p}>{p}</span>)
                        : <span className="muted">{lane.systems.map((s) => s.sid).join(", ")} · no routes</span>}
                    </dd>
                  </div>
                ))}
              </dl>
            </Link>
          );
        })}
      </div>
    </>
  );
}
