import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { Topology, TransportRoute } from "../types";
import { Pill } from "./ui";

interface Edge {
  route: TransportRoute;
  d: string;
  labelX: number;
  labelY: number;
  below: boolean;
}

const STATUS_TEXT: Record<string, string> = { fail: "✕ failed", review: "RC 4", ok: "", none: "" };

function edgeState(route: TransportRoute): "fail" | "review" | "ok" | "none" {
  const status = route.last_import?.status;
  if (!status) return "none";
  return status === "fail" ? "fail" : status === "review" ? "review" : "ok";
}

/**
 * Lanes (one per product track) x tiers (DEV → QAS → PRE → PRD). System cards sit in a CSS grid;
 * an SVG overlay draws the transport routes between the measured card positions.
 */
export function LandscapeDiagram({ topology }: { topology: Topology }) {
  const navigate = useNavigate();
  const container = useRef<HTMLDivElement>(null);
  const nodes = useRef(new Map<number, HTMLElement>());
  const [edges, setEdges] = useState<Edge[]>([]);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [focus, setFocus] = useState<number | null>(null);

  const measure = useCallback(() => {
    const root = container.current;
    if (!root) return;
    const base = root.getBoundingClientRect();
    setSize({ w: root.scrollWidth, h: root.scrollHeight });
    const box = (id: number) => {
      const el = nodes.current.get(id);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { l: r.left - base.left + root.scrollLeft, t: r.top - base.top, r: r.right - base.left + root.scrollLeft, b: r.bottom - base.top };
    };
    const all = new Map<number, NonNullable<ReturnType<typeof box>>>();
    nodes.current.forEach((_, id) => { const bx = box(id); if (bx) all.set(id, bx); });
    const next: Edge[] = [];
    for (const route of topology.routes) {
      const a = all.get(route.source_system_id);
      const b = all.get(route.target_system_id);
      if (!a || !b) continue;
      let d: string, lx: number, ly: number, below = false;
      if (b.l >= a.r - 4) {
        const x1 = a.r, y1 = (a.t + a.b) / 2, x2 = b.l - 2, y2 = (b.t + b.b) / 2;
        // Cards sitting between the two systems (skipped tiers) force the route underneath the row.
        const top = Math.min(y1, y2) - 8, bottom = Math.max(y1, y2) + 8;
        const obstacles = [...all.entries()].filter(([id, o]) =>
          id !== route.source_system_id && id !== route.target_system_id &&
          o.l < x2 && o.r > x1 && o.t < bottom && o.b > top).map(([, o]) => o);
        if (obstacles.length) {
          const yU = Math.max(a.b, b.b, ...obstacles.map((o) => o.b)) + 18;
          const xa = a.l + (a.r - a.l) * 0.72, xb = b.l + (b.r - b.l) * 0.28, r = 8;
          d = `M${xa},${a.b} L${xa},${yU - r} Q${xa},${yU} ${xa + r},${yU} L${xb - r},${yU} Q${xb},${yU} ${xb},${yU - r} L${xb},${b.b + 3}`;
          lx = (xa + xb) / 2;
          ly = yU;
          below = true;
        } else {
          const dx = Math.max(24, (x2 - x1) / 2);
          d = `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
          lx = (x1 + x2) / 2;
          ly = (y1 + y2) / 2;
        }
      } else {
        const down = b.t >= a.b;
        const x = a.l + 18, y1 = down ? a.b : a.t, y2 = down ? b.t - 3 : b.b + 3;
        d = `M${x},${y1} L${x},${y2}`;
        lx = x + 6;
        ly = (y1 + y2) / 2;
      }
      next.push({ route, d, labelX: lx, labelY: ly, below });
    }
    setEdges(next);
  }, [topology]);

  useLayoutEffect(() => {
    measure();
    const root = container.current;
    if (!root) return;
    const observer = new ResizeObserver(() => measure());
    observer.observe(root);
    return () => observer.disconnect();
  }, [measure]);

  const cols = `repeat(${topology.tiers.length}, minmax(150px, 1fr))`;
  const connected = (route: TransportRoute) => focus !== null && (route.source_system_id === focus || route.target_system_id === focus);

  return (
    <div className="diagram-scroll">
      <div className="diagram" ref={container} style={{ gridTemplateColumns: cols, minWidth: topology.tiers.length * 220 }}
           role="figure" aria-label={`Transport landscape ${topology.landscape}`}>
        {topology.tiers.map((t) => <div key={t.key} className="diagram-tier"><span className="tag">{t.key}</span> {t.label}</div>)}
        {topology.lanes.map((lane) => (
          <div key={lane.key} style={{ display: "contents" }}>
            <div className="lane-head">
              <strong>{lane.product_label}</strong>
              <span className="muted">{lane.stack === "JAVA" ? "CTS+ (Java)" : "TMS (ABAP)"}</span>
              {lane.path ? lane.path.split(" | ").map((p) => <span key={p} className="lane-path">{p}</span>)
                : <span className="muted">no routes yet</span>}
            </div>
            {topology.tiers.map((tier) => (
              <div key={tier.key} className="diagram-cell">
                {lane.systems.filter((s) => s.environment === tier.key).map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    className={`node ${focus === s.id ? "focus" : ""} ${s.enabled ? "" : "disabled"}`}
                    ref={(el) => { if (el) nodes.current.set(s.id, el); else nodes.current.delete(s.id); }}
                    onMouseEnter={() => setFocus(s.id)}
                    onMouseLeave={() => setFocus(null)}
                    onFocus={() => setFocus(s.id)}
                    onBlur={() => setFocus(null)}
                    onClick={() => navigate(`/systems/${s.id}`)}
                    title={`${s.sid} · ${s.name}`}
                  >
                    <span className="node-top"><span className="node-sid">{s.sid}</span><Pill value={s.health} /></span>
                    <span className="node-name">{s.name}</span>
                    <span className="node-meta">{s.database}{s.product_version ? ` · ${s.product_version}` : ""}{!s.enabled && " · disabled"}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        ))}
        <svg className="diagram-edges" width={size.w} height={size.h} aria-hidden="true">
          <defs>
            {["none", "ok", "review", "fail", "focus"].map((k) => (
              <marker key={k} id={`arrow-${k}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" className={`arrowhead ${k}`} />
              </marker>
            ))}
          </defs>
          {edges.map((e) => {
            const state = edgeState(e.route);
            const hot = connected(e.route);
            return (
              <g key={e.route.id} className={`edge ${state} ${hot ? "hot" : ""} ${focus !== null && !hot ? "dim" : ""}`}>
                <path d={e.d} markerEnd={`url(#arrow-${hot ? "focus" : state})`} className={e.route.route_type}>
                  <title>{`${e.route.source_sid} → ${e.route.target_sid}: ${e.route.route_type}${e.route.transport_layer ? `, layer ${e.route.transport_layer}` : ""}`}</title>
                </path>
                {(() => {
                  const vertical = !e.below && !e.d.includes("C");
                  const anchor = vertical ? "start" : "middle";
                  const status = STATUS_TEXT[state];
                  const cls = state === "fail" ? "edge-alert" : state === "review" ? "edge-warn" : undefined;
                  if (e.below) {
                    const text = [e.route.transport_layer, status].filter(Boolean).join(" · ");
                    return text ? <text x={e.labelX} y={e.labelY + 13} textAnchor="middle" className={cls}>{text}</text> : null;
                  }
                  return (
                    <>
                      {e.route.transport_layer && <text x={e.labelX} y={vertical ? e.labelY - 4 : e.labelY - 7} textAnchor={anchor}>{e.route.transport_layer}</text>}
                      {status && <text x={e.labelX} y={vertical ? e.labelY + 10 : e.labelY + 15} textAnchor={anchor} className={cls}>{status}</text>}
                    </>
                  );
                })()}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
