'use client';
import { useEffect, useMemo, useRef, useState, type JSX } from 'react';

/* A time-series line chart in plain SVG.
   =====================================
   The app carries no chart library and this is the only page that draws one, so a dependency
   for it would be paid on every page for the benefit of one operator tab. What the health tab
   needs is small: a few lines over time, gaps where the sampler was down, a crosshair that
   reads every series at once, and colours that follow the theme. That is what is here and
   nothing more — no zoom, no brushing, no animation.

   Sizing: the SVG is drawn at the wrapper's measured pixel width (ResizeObserver), NOT with a
   stretched `preserveAspectRatio="none"` viewBox. A stretched viewBox scales the text and the
   stroke width with it, so axis labels come out wide on a desktop and unreadable on a phone;
   drawing at real pixels keeps 11px text 11px everywhere.

   Gaps: a `null` value breaks the path (`M` again after it) rather than being skipped, because
   a skipped null would draw a straight line across the minutes the api was not sampling —
   which on this tab is exactly the outage the operator is looking for. */

export interface ChartPoint {
  /** Epoch milliseconds. */
  t: number;
  v: number | null;
}

export interface ChartSeries {
  name: string;
  /** Any CSS colour — the console passes its theme tokens (`var(--beam)` etc.). */
  color: string;
  points: ChartPoint[];
  /** A ceiling or a reference line rather than a measurement (memory total, for one). */
  dashed?: boolean;
}

export interface LineChartProps {
  series: ChartSeries[];
  height?: number;
  /** Appended to the y-axis labels and the tooltip values (`%`, ` MB`, ` ms`). */
  unit?: string;
  yMin?: number;
  yMax?: number;
  formatX: (t: number) => string;
  formatY?: (v: number) => string;
  /** Shown centred when no series has a single non-null point. */
  emptyText?: string;
}

const PAD = { top: 10, right: 12, bottom: 24, left: 46 };
const GRID_LINES = 4;

/** A "nice" step for the y gridlines: 1, 2, 2.5 or 5 × 10^k, whichever gives ~4 lines. */
function niceStep(span: number): number {
  if (!(span > 0)) return 1;
  const raw = span / GRID_LINES;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const r = raw / mag;
  const m = r <= 1 ? 1 : r <= 2 ? 2 : r <= 2.5 ? 2.5 : r <= 5 ? 5 : 10;
  return m * mag;
}

function defaultFormatY(v: number): string {
  if (Math.abs(v) >= 1000) return Math.round(v).toLocaleString();
  if (Number.isInteger(v)) return String(v);
  return v.toFixed(Math.abs(v) < 10 ? 1 : 0);
}

export function LineChart({
  series, height = 180, unit = '', yMin, yMax, formatX, formatY = defaultFormatY, emptyText = '—',
}: LineChartProps): JSX.Element {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hoverT, setHoverT] = useState<number | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver(entries => {
      for (const e of entries) setWidth(Math.floor(e.contentRect.width));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const model = useMemo(() => {
    let tMin = Infinity, tMax = -Infinity, vMin = Infinity, vMax = -Infinity;
    const ts = new Set<number>();
    for (const s of series) {
      for (const p of s.points) {
        ts.add(p.t);
        if (p.t < tMin) tMin = p.t;
        if (p.t > tMax) tMax = p.t;
        if (p.v !== null && Number.isFinite(p.v)) {
          if (p.v < vMin) vMin = p.v;
          if (p.v > vMax) vMax = p.v;
        }
      }
    }
    const hasData = vMin !== Infinity;
    /* Zero is the honest floor for every quantity this chart is used for (a percentage, a
       byte count, a rate), so the axis starts there unless told otherwise; a data-driven floor
       would turn a flat 60% CPU line into a dramatic mountain range. The top gets a little
       headroom and is then rounded up to a gridline. */
    let lo = yMin ?? Math.min(0, hasData ? vMin : 0);
    let hi = yMax ?? (hasData ? vMax * 1.08 : 1);
    if (hi <= lo) hi = lo + 1;
    const step = niceStep(hi - lo);
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;
    if (hi === lo) hi = lo + step;
    const yTicks: number[] = [];
    for (let v = lo; v <= hi + step / 2; v += step) yTicks.push(Number(v.toFixed(10)));
    const sortedTs = [...ts].sort((a, b) => a - b);
    return { tMin, tMax, lo, hi, yTicks, sortedTs, hasData };
  }, [series, yMin, yMax]);

  const w = Math.max(width, 200);
  const innerW = w - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom;
  const { tMin, tMax, lo, hi, yTicks, sortedTs, hasData } = model;
  const tSpan = tMax > tMin ? tMax - tMin : 1;
  const x = (t: number) => PAD.left + ((t - tMin) / tSpan) * innerW;
  const y = (v: number) => PAD.top + innerH - ((v - lo) / (hi - lo)) * innerH;

  const paths = series.map(s => {
    let d = '';
    let pen = false;
    for (const p of s.points) {
      if (p.v === null || !Number.isFinite(p.v)) { pen = false; continue; }
      d += `${pen ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`;
      pen = true;
    }
    return d;
  });

  /* 4–6 time labels. The count follows the width so a phone gets four and a wide desktop
     six, and each is placed on an even fraction of the span rather than on a data point, so a
     gap in the data does not leave a gap in the axis. */
  const xCount = innerW < 420 ? 4 : innerW < 720 ? 5 : 6;
  const xTicks = Array.from({ length: xCount }, (_, i) => tMin + (tSpan * i) / (xCount - 1));

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!sortedTs.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const t = tMin + ((px - PAD.left) / innerW) * tSpan;
    // Nearest sampled timestamp by binary search — the series share a time grid, so one
    // lookup answers for all of them.
    let a = 0, b = sortedTs.length - 1;
    while (a < b) {
      const m = (a + b) >> 1;
      if (sortedTs[m] < t) a = m + 1; else b = m;
    }
    const cand = a > 0 && Math.abs(sortedTs[a - 1] - t) < Math.abs(sortedTs[a] - t) ? a - 1 : a;
    setHoverT(sortedTs[cand]);
  };

  const hover = hoverT === null ? null : {
    t: hoverT,
    x: x(hoverT),
    values: series.map(s => {
      const p = s.points.find(q => q.t === hoverT);
      return { name: s.name, color: s.color, v: p && p.v !== null && Number.isFinite(p.v) ? p.v : null };
    }),
  };
  // Flip the tooltip to the left of the crosshair in the right half so it never leaves the box.
  const tipLeft = hover ? (hover.x > w / 2 ? undefined : hover.x + 10) : undefined;
  const tipRight = hover && hover.x > w / 2 ? w - hover.x + 10 : undefined;

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%' }}>
      <svg
        width={w}
        height={height}
        style={{ display: 'block', fontFamily: 'var(--font-sans)', fontSize: 11 }}
        onMouseMove={onMove}
        onMouseLeave={() => setHoverT(null)}
      >
        {yTicks.map(v => (
          <g key={v}>
            <line
              x1={PAD.left} x2={w - PAD.right} y1={y(v)} y2={y(v)}
              style={{ stroke: 'var(--hairline)', strokeOpacity: 0.7 }}
            />
            <text x={PAD.left - 6} y={y(v) + 3.5} textAnchor="end" style={{ fill: 'var(--muted)' }}>
              {formatY(v)}{unit}
            </text>
          </g>
        ))}
        {hasData ? xTicks.map((t, i) => (
          <text
            key={i}
            x={x(t)}
            y={height - 7}
            textAnchor={i === 0 ? 'start' : i === xTicks.length - 1 ? 'end' : 'middle'}
            style={{ fill: 'var(--muted)' }}
          >
            {formatX(t)}
          </text>
        )) : null}
        {series.map((s, i) => (
          <path
            key={s.name}
            d={paths[i]}
            fill="none"
            style={{
              stroke: s.color,
              strokeWidth: s.dashed ? 1.25 : 1.75,
              strokeDasharray: s.dashed ? '5 4' : undefined,
              strokeLinejoin: 'round',
              strokeLinecap: 'round',
            }}
          />
        ))}
        {hover ? (
          <g>
            <line
              x1={hover.x} x2={hover.x} y1={PAD.top} y2={PAD.top + innerH}
              style={{ stroke: 'var(--muted)', strokeDasharray: '3 3' }}
            />
            {hover.values.map(v => v.v === null ? null : (
              <circle key={v.name} cx={hover.x} cy={y(v.v)} r={3} style={{ fill: v.color }} />
            ))}
          </g>
        ) : null}
        {!hasData ? (
          <text
            x={PAD.left + innerW / 2} y={PAD.top + innerH / 2}
            textAnchor="middle" style={{ fill: 'var(--muted)', fontSize: 12.5 }}
          >
            {emptyText}
          </text>
        ) : null}
      </svg>
      {hover ? (
        <div
          style={{
            position: 'absolute', top: PAD.top, left: tipLeft, right: tipRight,
            pointerEvents: 'none', padding: '6px 9px', borderRadius: 'var(--r-sm)',
            background: 'var(--card-solid)', border: '1px solid var(--hairline)',
            boxShadow: 'var(--shadow)', fontSize: 11.5, whiteSpace: 'nowrap', zIndex: 2,
          }}
        >
          <div style={{ color: 'var(--muted)', marginBottom: 3 }}>{formatX(hover.t)}</div>
          {hover.values.map(v => (
            <div key={v.name} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: v.color, flex: 'none' }} />
              <span style={{ color: 'var(--mist)' }}>{v.name}</span>
              <b style={{ marginLeft: 'auto', fontVariantNumeric: 'tabular-nums' }}>
                {v.v === null ? '—' : `${formatY(v.v)}${unit}`}
              </b>
            </div>
          ))}
        </div>
      ) : null}
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 4, fontSize: 11.5, color: 'var(--muted)' }}>
        {series.map(s => (
          <span key={s.name} style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            <span
              style={{
                width: 14, height: 0, borderTop: `2px ${s.dashed ? 'dashed' : 'solid'} ${s.color}`,
                display: 'inline-block',
              }}
            />
            {s.name}
          </span>
        ))}
      </div>
    </div>
  );
}
