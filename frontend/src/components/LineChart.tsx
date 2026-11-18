import { TrendArea } from "./charts";

export interface ChartMarker { t: number; label: string; severity: string; }

export interface Point {
  t: number; // epoch ms
  v: number;
}

/** Time-series chart used by the monitor tabs; rendered as a gradient area chart. */
export function LineChart({ points, unit, height = 180, threshold, label, markers = [] }: {
  points: Point[];
  unit: string;
  height?: number;
  threshold?: { value: number; label: string };
  label: string;
  markers?: ChartMarker[];
}) {
  return <TrendArea points={points} unit={unit} height={height} threshold={threshold} label={label} markers={markers} />;
}
