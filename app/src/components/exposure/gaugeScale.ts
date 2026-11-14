// Geometry + scale helpers for ExposureGauge only. Pure functions, no React.
import type { ExposureLevel } from '../../lib/hav';

/** SVG geometry: viewBox 0 0 240 150, semicircle centred low so the big number sits inside the arc. */
export const GAUGE = { cx: 120, cy: 124, r: 96, stroke: 14 } as const;

/**
 * Upper end of the scale. Capped a little above the limit (x1.125 → 450 for a 400 limit) so the
 * 400 tick is not jammed against the arc end, and stretched if a projection would overflow it.
 */
export function gaugeMax(action: number, limit: number, projected: number): number {
  return Math.max(limit * 1.125, projected * 1.05, action * 2);
}

/**
 * Piecewise-linear split scale, value → fraction of the arc (0..1).
 *
 * Why not linear? On a linear 0–450 scale the action value (100) would sit at ~22% of the arc and a
 * typical mid-shift reading like 85 would look like a sliver, even though it is 85% of the way to
 * the threshold that actually matters for today's decision. The split scale gives the decision zone
 * 0–action the first half of the arc and compresses action–max into the second half, so 100 sits at
 * the top of the arc, 400 is still clearly marked near the right end, and small changes below the
 * action value stay legible. Tick labels at 0 / action / limit make the non-linearity explicit.
 */
export function makeFrac(action: number, max: number) {
  return (v: number): number => {
    const x = Math.max(0, Math.min(v, max));
    if (x <= action) return (x / action) * 0.5;
    return 0.5 + ((x - action) / (max - action)) * 0.5;
  };
}

/** Point on the upper semicircle for a 0..1 fraction (0 = left end, 1 = right end). */
export function polar(frac: number, radius: number = GAUGE.r) {
  const a = Math.PI * (1 - frac);
  return { x: GAUGE.cx + radius * Math.cos(a), y: GAUGE.cy - radius * Math.sin(a) };
}

/** Arc path between two fractions, drawn clockwise over the top. Never exceeds 180°, so large-arc = 0. */
export function arcPath(from: number, to: number, radius: number = GAUGE.r): string {
  const p0 = polar(from, radius);
  const p1 = polar(Math.max(to, from + 0.0001), radius);
  return `M ${p0.x.toFixed(2)} ${p0.y.toFixed(2)} A ${radius} ${radius} 0 0 1 ${p1.x.toFixed(2)} ${p1.y.toFixed(2)}`;
}

/** Static class strings (Tailwind must see them literally). */
export const gaugeStroke: Record<ExposureLevel, string> = { below: 'stroke-ok', action: 'stroke-warn', limit: 'stroke-bad' };
