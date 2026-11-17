import { useMemo, useRef, useState } from "react";

export interface Point {
  t: number; // epoch ms
  v: number;
}

/**
 * Single-series line chart: one hue, 2px line, >=8px markers, recessive grid,
 * crosshair + tooltip on hover, table fallback via the caller. The title names the series.
 */
export function LineChart({ points, unit, height = 180, threshold, label }: {
  points: Point[];
  unit: string;
  height?: number;
  threshold?: { value: number; label: string };
  label: string;
}) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const width = 640;
  const pad = { l: 56, r: 16, t: 12, b: 26 };

  const geo = useMemo(() => {
    const ts = points.map((p) => p.t);
    const vs = points.map((p) => p.v).concat(threshold ? [threshold.value] : []);
    const t0 = Math.min(...ts), t1 = Math.max(...ts);
    let v0 = Math.min(...vs), v1 = Math.max(...vs);
    if (v0 === v1) { v0 -= 1; v1 += 1; }
    const span = v1 - v0;
    v0 = Math.max(0, v0 - span * 0.1);
    v1 = v1 + span * 0.1;
    const x = (t: number) => pad.l + (t1 === t0 ? 0.5 : (t - t0) / (t1 - t0)) * (width - pad.l - pad.r);
    const y = (v: number) => pad.t + (1 - (v - v0) / (v1 - v0)) * (height - pad.t - pad.b);
    const ticks = Array.from({ length: 4 }, (_, i) => v0 + ((v1 - v0) * i) / 3);
    return { x, y, ticks, t0, t1 };
  }, [points, threshold, height]);

  if (points.length < 2) {
    return <p className="muted">Collect at least two observations to see a trend.</p>;
  }
  const path = points.map((p, i) => `${i ? "L" : "M"}${geo.x(p.t).toFixed(1)},${geo.y(p.v).toFixed(1)}`).join(" ");
  const fmt = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 1 });
  const day = (t: number) => new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" });

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = ref.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    let best = 0;
    points.forEach((p, i) => {
      if (Math.abs(geo.x(p.t) - px) < Math.abs(geo.x(points[best].t) - px)) best = i;
    });
    setHover(best);
  };
  const h = hover !== null ? points[hover] : null;

  return (
    <div className="chart">
      <svg ref={ref} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}
           onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {geo.ticks.map((tick) => (
          <g key={tick}>
            <line x1={pad.l} x2={width - pad.r} y1={geo.y(tick)} y2={geo.y(tick)} className="grid" />
            <text x={pad.l - 8} y={geo.y(tick) + 4} textAnchor="end" className="axis">{fmt(tick)}</text>
          </g>
        ))}
        <text x={pad.l} y={height - 6} className="axis">{day(geo.t0)}</text>
        <text x={width - pad.r} y={height - 6} textAnchor="end" className="axis">{day(geo.t1)}</text>
        {threshold && (
          <g>
            <line x1={pad.l} x2={width - pad.r} y1={geo.y(threshold.value)} y2={geo.y(threshold.value)} className="threshold" />
            <text x={width - pad.r} y={geo.y(threshold.value) - 5} textAnchor="end" className="axis">{threshold.label}</text>
          </g>
        )}
        <path d={path} className="series" />
        {points.map((p, i) => (
          <circle key={p.t} cx={geo.x(p.t)} cy={geo.y(p.v)} r={hover === i ? 5 : 4} className="marker" />
        ))}
        {h && <line x1={geo.x(h.t)} x2={geo.x(h.t)} y1={pad.t} y2={height - pad.b} className="crosshair" />}
      </svg>
      {h && (
        <div className="tooltip" style={{ left: `${(geo.x(h.t) / width) * 100}%` }}>
          <strong>{fmt(h.v)} {unit}</strong>
          <span>{new Date(h.t).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</span>
        </div>
      )}
    </div>
  );
}
