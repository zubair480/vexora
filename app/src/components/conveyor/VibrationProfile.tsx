// Vibration-vs-baseline profile along the belt, on the same x scale as the route scene above it.
// Built only from each station's vibrationDelta (stations without one sit on the baseline), so it
// is a visual summary of the station data, not an extra data source. RouteView shows it only when
// the card has spare height.
import type { Station, Status } from '../../data/types';
import { VB, pctX, stationX } from './route-geometry';

const statusVar: Record<Status, string> = { ok: '--ok', attention: '--warn', defect: '--bad' };
const c = (name: string, a?: number) => (a == null ? `rgb(var(${name}))` : `rgb(var(${name}) / ${a})`);

const W = VB.w;
const H = 100;
const BASE = 84; // baseline y
const TOP = 14; // y of the highest peak
const SIGMA = 46;

export function VibrationProfile({ stations, idPrefix }: { stations: Station[]; idPrefix: string }) {
  const peaks = stations.map((s) => ({ x: stationX(s.position), d: Math.max(0, s.vibrationDelta ?? 0) }));
  const maxD = Math.max(1, ...peaks.map((p) => p.d));
  const peak = stations.reduce<Station | null>((best, s) => ((s.vibrationDelta ?? 0) > (best?.vibrationDelta ?? 0) ? s : best), null);

  const y = (x: number) => {
    let v = 0;
    for (const p of peaks) v += p.d * Math.exp(-(((x - p.x) / SIGMA) ** 2));
    return BASE - (Math.min(v, maxD) / maxD) * (BASE - TOP);
  };
  const pts: string[] = [];
  for (let x = 0; x <= W; x += 6) pts.push(`${x},${y(x).toFixed(1)}`);
  const line = `M ${pts.join(' L ')}`;
  const area = `${line} L ${W},${BASE} L 0,${BASE} Z`;
  const gid = `${idPrefix}-vib`;

  return (
    <div className="relative" aria-label={peak ? `Vibration along route: peak +${peak.vibrationDelta}% at ${peak.name}` : 'Vibration along route: at baseline'}>
      <div className="mb-1 flex items-baseline justify-between gap-3 text-[11px]">
        <span className="text-ink-2">Vibration vs baseline along route</span>
        {peak && peak.vibrationDelta ? (
          <span className="tabular-nums text-ink-3">
            Peak <span className="font-semibold text-bad">+{peak.vibrationDelta}%</span> · {peak.name}
          </span>
        ) : (
          <span className="text-ink-3">All stations at baseline</span>
        )}
      </div>
      <div className="relative h-[52px]">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 h-full w-full" aria-hidden>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={c('--bad', 0.28)} />
              <stop offset="1" stopColor={c('--bad', 0)} />
            </linearGradient>
          </defs>
          <line x1={0} y1={BASE} x2={W} y2={BASE} stroke={c('--line')} strokeWidth={1} vectorEffect="non-scaling-stroke" />
          <path d={area} fill={`url(#${gid})`} />
          <path d={line} fill="none" stroke={c('--bad', 0.85)} strokeWidth={1.75} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          {stations.map((s) => (
            <line
              key={s.id}
              x1={stationX(s.position)} x2={stationX(s.position)} y1={BASE} y2={H}
              stroke={c(statusVar[s.status], 0.7)} strokeWidth={1.5} vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
        {/* status dots in HTML so they stay round under the stretched SVG */}
        {stations.map((s) => (
          <span
            key={s.id}
            className="absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface"
            style={{ left: `${pctX(stationX(s.position))}%`, top: `${(BASE / H) * 100}%`, background: c(statusVar[s.status]) }}
          />
        ))}
      </div>
    </div>
  );
}
