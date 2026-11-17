import { Link } from "react-router-dom";
import { ArrowRight, Plus } from "lucide-react";
import { api } from "../api";
import { Empty, ErrorBanner, PageHeader, Pill, StatusDot, statusTone, useAsync } from "../components/ui";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import type { Topology } from "../types";

export default function Landscapes() {
  const { data, error, loading } = useAsync(async () => {
    const list = await api.landscapes();
    return Promise.all(list.map((l) => api.topology(l.landscape)));
  }, []);

  return (
    <>
      <PageHeader title="Landscapes" description="Product tracks and the transport paths between their systems."
        actions={<Button asChild><Link to="/systems?new=1" className="hover:no-underline"><Plus />Add system</Link></Button>} />
      <ErrorBanner error={error} />
      {loading && !data && <div className="grid gap-4 md:grid-cols-2">{[0, 1].map((i) => <Skeleton key={i} className="h-48" />)}</div>}
      {!loading && !data?.length && <Empty>No landscapes yet. Every system belongs to a landscape — <Link to="/systems?new=1">add a system</Link>.</Empty>}
      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
        {data?.map((t: Topology) => {
          const systems = t.lanes.flatMap((l) => l.systems);
          const worst = ["critical", "warning", "healthy"].find((h) => systems.some((s) => s.health === h)) ?? "unknown";
          return (
            <section key={t.landscape} className="flex flex-col rounded-xl border bg-card p-5 shadow-[0_1px_2px_hsl(var(--shadow)/0.06)]">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2"><StatusDot tone={statusTone(worst)} /><h2 className="text-base">{t.landscape}</h2></div>
                  <div className="mt-1 text-xs text-muted-foreground">{systems.length} systems, {t.routes.length} transport routes</div>
                </div>
                <Pill value={worst} />
              </div>
              <div className="mt-4 space-y-3">
                {t.lanes.map((lane) => (
                  <div key={lane.key}>
                    <div className="text-xs font-medium text-muted-foreground">{lane.product_label}</div>
                    {(lane.path ? lane.path.split(" | ") : [lane.systems.map((s) => s.sid).join(", ")]).map((path) => (
                      <div key={path} className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        {path.split(/ → |, /).map((sid, i, all) => {
                          const target = lane.systems.find((s) => s.sid === sid);
                          return (
                            <span key={i} className="flex items-center gap-1.5">
                              {target ? (
                                <Link to={`/systems/${target.id}`} className="hop hover:border-primary/50 hover:no-underline">
                                  <StatusDot tone={statusTone(target.health)} className="mr-1.5" />{sid}
                                </Link>
                              ) : <span className="hop">{sid}</span>}
                              {lane.path && i < all.length - 1 && <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />}
                            </span>
                          );
                        })}
                        {!lane.path && <span className="text-xs text-muted-foreground">no routes defined</span>}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
              <div className="mt-auto pt-4">
                <Link to={`/landscapes/${encodeURIComponent(t.landscape)}`} className="inline-flex items-center gap-1 text-sm font-medium">
                  Open route diagram<ArrowRight className="h-3.5 w-3.5" />
                </Link>
              </div>
            </section>
          );
        })}
      </div>
    </>
  );
}
