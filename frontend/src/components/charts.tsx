import { useId, type ReactNode } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart as RLineChart, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { cn } from "@/lib/utils";

/** Fixed categorical order: series keep their colour whatever the filter (never cycled by rank). */
export const SERIES_COLORS = [
  "hsl(var(--primary))", "hsl(var(--foreground))", "hsl(199 89% 48%)", "hsl(292 70% 62%)", "hsl(170 60% 40%)", "hsl(25 85% 55%)",
];

const fmtTime = (t: number, spanMs: number) => {
  const d = new Date(t);
  return spanMs > 2 * 86400000
    ? d.toLocaleDateString(undefined, { day: "2-digit", month: "short" })
    : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
};
const fmtNum = (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })}k`
  : v.toLocaleString(undefined, { maximumFractionDigits: 1 }));

function ChartTooltip({ active, payload, label, unit, spanMs, names }: {
  active?: boolean; payload?: { value: number; dataKey: string; color: string }[]; label?: number; unit: string; spanMs: number;
  names?: Record<string, string>;
}) {
  if (!active || !payload?.length || label == null) return null;
  return (
    <div className="rounded-2xl border bg-popover px-3 py-2 text-xs shadow-xl">
      <div className="mb-1 text-muted-foreground">{new Date(label).toLocaleString(undefined, spanMs > 2 * 86400000
        ? { dateStyle: "medium" } : { dateStyle: "medium", timeStyle: "short" })}</div>
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full" style={{ background: p.color }} />
          {names && <span className="text-muted-foreground">{names[p.dataKey] ?? p.dataKey}</span>}
          <span className="ml-auto font-semibold tabular-nums">{fmtNum(p.value)}{unit === "%" ? "%" : unit ? ` ${unit}` : ""}</span>
        </div>
      ))}
    </div>
  );
}

export interface TimePoint { t: number; v: number }

/** Area time-series with gradient fill, optional threshold and incident markers. */
export function TrendArea({ points, unit = "", height = 200, threshold, markers = [], color = "hsl(var(--primary))", label }: {
  points: TimePoint[]; unit?: string; height?: number; threshold?: { value: number; label: string };
  markers?: { t: number; label: string; severity: string }[]; color?: string; label?: string;
}) {
  const id = useId().replace(/:/g, "");
  if (points.length < 2) {
    return <div className="flex items-center justify-center rounded-2xl bg-surface text-xs text-muted-foreground" style={{ height }}>
      Collect at least two observations to see a trend.</div>;
  }
  const span = points[points.length - 1].t - points[0].t;
  return (
    <div style={{ height }} role="img" aria-label={label}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
          <defs>
            <linearGradient id={`g${id}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.32} />
              <stop offset="100%" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} strokeDasharray="3 4" />
          <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} scale="time" tickFormatter={(t) => fmtTime(t, span)}
                 tickLine={false} axisLine={false} minTickGap={36} tickMargin={8} />
          <YAxis tickLine={false} axisLine={false} width={48} tickFormatter={fmtNum} />
          <Tooltip content={<ChartTooltip unit={unit} spanMs={span} />} />
          {threshold && <ReferenceLine y={threshold.value} stroke="hsl(var(--warn))" strokeDasharray="5 5"
            label={{ value: threshold.label, position: "insideTopRight", fill: "hsl(var(--warn))", fontSize: 11 }} />}
          {markers.map((m) => (
            <ReferenceLine key={`${m.t}-${m.label}`} x={m.t} stroke={m.severity === "critical" ? "hsl(var(--bad))" : "hsl(var(--warn))"} strokeDasharray="3 3" />
          ))}
          <Area type="monotone" dataKey="v" stroke={color} strokeWidth={2.5} fill={`url(#g${id})`} dot={false}
                activeDot={{ r: 5, strokeWidth: 3, stroke: "hsl(var(--surface))" }} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Several systems' series on one time axis (line per system, fixed colour order, legend always shown). */
export function MultiTrend({ series, unit = "", height = 260 }: {
  series: { key: string; label: string; points: TimePoint[] }[]; unit?: string; height?: number;
}) {
  const rows = new Map<number, Record<string, number>>();
  series.forEach((s) => s.points.forEach((p) => {
    const row = rows.get(p.t) ?? { t: p.t };
    row[s.key] = p.v;
    rows.set(p.t, row);
  }));
  const data = [...rows.values()].sort((a, b) => a.t - b.t);
  if (data.length < 2) {
    return <div className="flex items-center justify-center rounded-2xl bg-surface text-xs text-muted-foreground" style={{ height }}>
      Not enough history yet. Trends appear after a few collector runs.</div>;
  }
  const span = data[data.length - 1].t - data[0].t;
  const names = Object.fromEntries(series.map((s) => [s.key, s.label]));
  return (
    <div>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <RLineChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
            <CartesianGrid vertical={false} strokeDasharray="3 4" />
            <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} scale="time" tickFormatter={(t) => fmtTime(t, span)}
                   tickLine={false} axisLine={false} minTickGap={40} tickMargin={8} />
            <YAxis tickLine={false} axisLine={false} width={48} tickFormatter={fmtNum} domain={["auto", "auto"]} />
            <Tooltip content={<ChartTooltip unit={unit} spanMs={span} names={names} />} />
            {series.map((s, i) => (
              <Line key={s.key} type="monotone" dataKey={s.key} stroke={SERIES_COLORS[i % SERIES_COLORS.length]} strokeWidth={2.2}
                    dot={false} connectNulls activeDot={{ r: 4 }} isAnimationActive={false} />
            ))}
          </RLineChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {series.map((s, i) => (
          <span key={s.key} className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} />{s.label}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Tiny area sparkline for KPI cards. */
export function Sparkline({ points, className, color = "hsl(var(--primary))" }: { points: TimePoint[]; className?: string; color?: string }) {
  const id = useId().replace(/:/g, "");
  if (points.length < 2) return <div className={cn("h-12 w-24", className)} />;
  return (
    <div className={cn("h-12 w-24", className)} aria-hidden="true">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={`s${id}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.3} /><stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area type="monotone" dataKey="v" stroke={color} strokeWidth={2} fill={`url(#s${id})`} dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Thin vertical bars with tiny labels (the reference's "J J A S O N" mini chart). */
export function MiniBars({ values, labels, highlightLast = true, className }: {
  values: number[]; labels?: string[]; highlightLast?: boolean; className?: string;
}) {
  const max = Math.max(1, ...values);
  return (
    <div className={cn("flex items-end gap-1.5", className)} aria-hidden="true">
      {values.map((v, i) => (
        <div key={i} className="flex flex-col items-center gap-1">
          <div className="flex h-10 w-2.5 items-end overflow-hidden rounded-full bg-muted">
            <div className={cn("w-full rounded-full", highlightLast && i === values.length - 1 ? "bg-primary" : "bg-primary/35")}
                 style={{ height: `${Math.max(6, (v / max) * 100)}%` }} />
          </div>
          {labels && <span className="text-[9px] leading-none text-muted-foreground">{labels[i]}</span>}
        </div>
      ))}
    </div>
  );
}

/** Stacked daily bars (e.g. critical / warning incidents per day). */
export function StackedBars({ data, keys, height = 220 }: {
  data: Record<string, number | string>[]; keys: { key: string; label: string; color: string }[]; height?: number;
}) {
  return (
    <div>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 4, left: -20, bottom: 0 }} barCategoryGap="28%">
            <CartesianGrid vertical={false} strokeDasharray="3 4" />
            <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} interval="preserveStartEnd" />
            <YAxis tickLine={false} axisLine={false} allowDecimals={false} width={40} />
            <Tooltip cursor={{ fill: "hsl(var(--accent) / 0.5)", radius: 8 }}
              content={({ active, payload, label }) => active && payload?.length ? (
                <div className="rounded-2xl border bg-popover px-3 py-2 text-xs shadow-xl">
                  <div className="mb-1 text-muted-foreground">{label}</div>
                  {payload.map((p) => (
                    <div key={String(p.dataKey)} className="flex items-center gap-2">
                      <span className="h-2 w-2 rounded-full" style={{ background: p.color }} />
                      <span className="text-muted-foreground">{keys.find((k) => k.key === p.dataKey)?.label}</span>
                      <span className="ml-auto font-semibold">{p.value as number}</span>
                    </div>
                  ))}
                </div>
              ) : null} />
            {keys.map((k, i) => (
              <Bar key={k.key} dataKey={k.key} stackId="a" fill={k.color} isAnimationActive={false}
                   radius={i === keys.length - 1 ? [6, 6, 0, 0] : [0, 0, 0, 0]} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 flex gap-4 text-xs text-muted-foreground">
        {keys.map((k) => <span key={k.key} className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: k.color }} />{k.label}</span>)}
      </div>
    </div>
  );
}

/**
 * Radial gauge drawn as dashes (the reference's "Budget statistic" ring).
 * Segments are coloured in order; the remainder stays muted.
 */
export function SegmentGauge({ segments, total, center, caption, size = 220 }: {
  segments: { value: number; color: string }[]; total: number; center: ReactNode; caption?: ReactNode; size?: number;
}) {
  const ticks = 60;
  const r1 = size / 2 - 6, r0 = r1 - 22;
  const sweep = 300, start = 120; // degrees, open at the bottom
  const bounds: { upto: number; color: string }[] = [];
  let acc = 0;
  segments.forEach((s) => { acc += s.value; bounds.push({ upto: total ? (acc / total) * ticks : 0, color: s.color }); });
  return (
    <div className="relative mx-auto" style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} className="h-full w-full" aria-hidden="true">
        {Array.from({ length: ticks }, (_, i) => {
          const a = ((start + (sweep * i) / (ticks - 1)) * Math.PI) / 180;
          const c = size / 2;
          const color = bounds.find((b) => i < Math.round(b.upto))?.color ?? "hsl(var(--muted))";
          return <line key={i} x1={c + r0 * Math.cos(a)} y1={c + r0 * Math.sin(a)} x2={c + r1 * Math.cos(a)} y2={c + r1 * Math.sin(a)}
                       stroke={color} strokeWidth={5} strokeLinecap="round" />;
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
        <div className="text-5xl font-bold tracking-tight tabular-nums">{center}</div>
        {caption && <div className="mt-1 max-w-[60%] text-xs text-muted-foreground">{caption}</div>}
      </div>
    </div>
  );
}

/** Small circular progress (the reference's "60% Completed" pill). */
export function RingProgress({ value, size = 28 }: { value: number; size?: number }) {
  const r = size / 2 - 3, c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true" className="-rotate-90">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="hsl(var(--muted))" strokeWidth={3} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="hsl(var(--primary))" strokeWidth={3} strokeLinecap="round"
              strokeDasharray={c} strokeDashoffset={c * (1 - Math.max(0, Math.min(1, value)))} />
    </svg>
  );
}
