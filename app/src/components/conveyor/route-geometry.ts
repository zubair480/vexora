// Pure geometry for the conveyor route illustration. Everything is in viewBox units (1200 x 500);
// HTML overlays use pctX / pctY so they stay locked to the SVG at any card width.
// The extra height below the ground line is room for the defect callout, so it never covers
// the defective roller even at compact density with its meta line shown.
import type { Station } from '../../data/types';

export const VB = { w: 1200, h: 500 } as const;

/** Head pulley centre (left, drive) and tail pulley centre (right, take-up). */
export const X0 = 112;
export const X1 = 1088;

/** Top surface of the carry run and its visible edge thickness. */
export const Y_TOP = 192;
export const BELT_T = 14;
/** Pulley radius (steel face); belt wraps it with BELT_T on the outside. */
export const R = 40;
export const PULLEY_CY = Y_TOP + BELT_T + R; // 246
/** Return run: top of the belt edge under the structure. */
export const Y_RET = PULLEY_CY + R; // 286
export const Y_GROUND = 366;

/** Oblique depth offset used for every "2.5D" solid (belt top face, rollers, pulleys). */
export const D = { dx: 20, dy: -24 } as const;

/** Vertical centre of the station chips band (above the belt). */
export const CHIP_Y = 56;

export const CARRY_R = 8;
export const CARRY_CY = Y_TOP + BELT_T + CARRY_R + 1;
export const RETURN_R = 9;
export const RETURN_CY = Y_RET + BELT_T + RETURN_R + 1;
export const DEFECT_R = 12;

export const CARRY_PITCH = 49;
export const RETURN_PITCH = 98;

export const stationX = (position: number) => X0 + position * (X1 - X0);

export const isReturnStation = (s: Station) => /return/i.test(s.role);

/** Where a station's marker sits: on the return idler for return stations, otherwise on the carry run. */
export function markerY(s: Station) {
  return isReturnStation(s) ? RETURN_CY + 3 : Y_TOP - 8;
}

export const pctX = (x: number) => (x / VB.w) * 100;
export const pctY = (y: number) => (y / VB.h) * 100;

/** Evenly pitched positions in [lo, hi] that include `anchor`, so a roller sits exactly under a station. */
export function pitched(anchor: number, pitch: number, lo: number, hi: number) {
  const out: number[] = [];
  let x = anchor - Math.floor((anchor - lo) / pitch) * pitch;
  for (; x <= hi; x += pitch) out.push(Math.round(x * 10) / 10);
  return out;
}

/** Oblique cylinder (axis along D) as a hull polygon + back/front disc centres. */
export function cylinder(cx: number, cy: number, r: number) {
  // Unit perpendicular to the depth axis D, scaled to r.
  const len = Math.hypot(D.dx, D.dy);
  const kx = (-D.dy / len) * r;
  const ky = (D.dx / len) * r;
  const bx = cx + D.dx;
  const by = cy + D.dy;
  const hull = [
    [cx + kx, cy + ky],
    [bx + kx, by + ky],
    [bx - kx, by - ky],
    [cx - kx, cy - ky],
  ]
    .map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`)
    .join(' ');
  return { hull, bx, by };
}

/** Small deterministic PRNG so the material on the belt never reshuffles between renders. */
export function seeded(seed: number) {
  let t = seed >>> 0;
  return () => {
    t += 0x6d2b79f5;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r ^= r + Math.imul(r ^ (r >>> 7), 61 | r);
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}

export interface Pebble { x: number; y: number; rx: number; ry: number; tone: 'ink' | 'brand' | 'light' }

/** Soft rounded pile of bulk material sitting on the belt top face. */
export function materialProfile(seed = 7) {
  const rnd = seeded(seed);
  const start = X0 + 58;
  const end = X1 - 66;
  const base = Y_TOP - 2;
  let d = `M ${start} ${base} `;
  let x = start;
  let prevTop = base - 6;
  d += `Q ${start + 6} ${base - 8} ${start + 16} ${prevTop - 4} `;
  x = start + 16;
  const crest: [number, number][] = [];
  while (x < end - 30) {
    const w = 26 + rnd() * 16;
    const amp = 16 + rnd() * 14;
    const nx = Math.min(x + w, end - 16);
    const top = base - amp;
    const ny = base - 9 - rnd() * 7;
    d += `Q ${(x + w / 2).toFixed(1)} ${(top - 4).toFixed(1)} ${nx.toFixed(1)} ${ny.toFixed(1)} `;
    crest.push([x + w / 2, top + 2]);
    x = nx;
    prevTop = ny;
  }
  d += `Q ${end - 4} ${prevTop + 2} ${end} ${base} Z`;

  const pebbles: Pebble[] = [];
  for (let i = 0; i < 70; i++) {
    const [cx, cy] = crest[Math.floor(rnd() * crest.length)];
    const r = 1.6 + rnd() * 2.6;
    pebbles.push({
      x: cx + (rnd() - 0.5) * 26,
      y: cy + rnd() * 9,
      rx: r * (1.1 + rnd() * 0.4),
      ry: r,
      tone: rnd() < 0.22 ? 'brand' : rnd() < 0.5 ? 'light' : 'ink',
    });
  }
  return { d, pebbles };
}

/** "Elevated vibration · likely bearing wear" -> "Elevated vibration · Likely bearing wear". */
export function sentenceCaseParts(text: string) {
  return text
    .split('·')
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(' · ');
}
