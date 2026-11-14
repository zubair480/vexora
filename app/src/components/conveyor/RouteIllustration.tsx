// The SVG "digital twin" of the conveyor: structure, belt, rollers, pulleys, material, flow and
// station markers. Pure presentation; RouteView owns the interaction state.
import { createContext, memo, useContext, useMemo } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import type { Station, Status } from '../../data/types';
import {
  BELT_T, CARRY_CY, CARRY_PITCH, CARRY_R, CHIP_Y, D, DEFECT_R, PULLEY_CY, R, RETURN_CY, RETURN_PITCH, RETURN_R,
  VB, X0, X1, Y_GROUND, Y_RET, Y_TOP, cylinder, isReturnStation, markerY, materialProfile, pitched, stationX,
} from './route-geometry';

const statusVar: Record<Status, string> = { ok: '--ok', attention: '--warn', defect: '--bad' };
const c = (name: string, a?: number) => (a == null ? `rgb(var(${name}))` : `rgb(var(${name}) / ${a})`);

/** Per-instance prefix for SVG defs, so two route maps on one page never share gradient or clip ids. */
const SvgIdContext = createContext('rg');
function svgIds(prefix: string) {
  return { id: (n: string) => `${prefix}-${n}`, url: (n: string) => `url(#${prefix}-${n})` };
}
function useSvgIds() {
  return svgIds(useContext(SvgIdContext));
}

/* ---------- static structure (memoised: never re-renders on hover) ---------- */

function Roller({ x, y, r, bad = false }: { x: number; y: number; r: number; bad?: boolean }) {
  const { url } = useSvgIds();
  const { hull, bx, by } = cylinder(x, y, r);
  return (
    <g>
      <circle cx={bx} cy={by} r={r} fill={bad ? c('--bad', 0.8) : c('--ink-3', 0.55)} />
      <polygon points={hull} fill={bad ? url('rollerBad') : url('roller')} opacity={bad ? 1 : 0.7} />
      <circle cx={x} cy={y} r={r} fill={bad ? c('--bad') : c('--panel')} stroke={bad ? c('--bad') : c('--ink-3')} strokeWidth={1.2} />
      <circle cx={x} cy={y} r={r * 0.36} fill={bad ? c('--bad-bg') : c('--ink-2', 0.8)} />
    </g>
  );
}

function Pulley({ x, side, spin }: { x: number; side: 'head' | 'tail'; spin: boolean }) {
  const { url } = useSvgIds();
  const { hull, bx, by } = cylinder(x, PULLEY_CY, R + BELT_T);
  const wrapR = R + BELT_T / 2;
  // Belt wraps the outer half: left half for the head, right half for the tail.
  const sweep = side === 'head' ? 0 : 1;
  const wrap = `M ${x} ${PULLEY_CY - wrapR} A ${wrapR} ${wrapR} 0 0 ${sweep} ${x} ${PULLEY_CY + wrapR}`;
  return (
    <g>
      <circle cx={bx} cy={by} r={R + BELT_T} fill={c('--ink', 0.55)} />
      <polygon points={hull} fill={url('beltTop')} />
      <circle cx={x} cy={PULLEY_CY} r={R} fill={url('steel')} stroke={c('--ink', 0.22)} strokeWidth={1.5} />
      <path d={wrap} fill="none" stroke={url('beltSide')} strokeWidth={BELT_T} />
      <motion.g
        style={{ transformBox: 'fill-box', transformOrigin: 'center' }}
        animate={spin ? { rotate: 360 } : undefined}
        transition={spin ? { repeat: Infinity, ease: 'linear', duration: 9 } : undefined}
      >
        <circle cx={x} cy={PULLEY_CY} r={R - 7} fill="none" stroke={c('--ink-3', 0.35)} strokeWidth={1} />
        {Array.from({ length: 6 }, (_, i) => {
          const a = (i * Math.PI) / 3;
          return (
            <line key={i} x1={x + Math.cos(a) * 12} y1={PULLEY_CY + Math.sin(a) * 12} x2={x + Math.cos(a) * (R - 8)} y2={PULLEY_CY + Math.sin(a) * (R - 8)}
              stroke={c('--ink-3', 0.6)} strokeWidth={3} strokeLinecap="round" />
          );
        })}
      </motion.g>
      <circle cx={x} cy={PULLEY_CY} r={13} fill={c('--ink-2')} />
      <circle cx={x} cy={PULLEY_CY} r={5} fill={c('--panel')} />
    </g>
  );
}

/** Yellow safety guard cage, echoing the guards in the reference shots. */
function Guard({ x, w }: { x: number; w: number }) {
  const top = Y_TOP - 12;
  const bottom = Y_GROUND - 4;
  const bars = Math.round(w / 14);
  return (
    <g stroke={c('--brand', 0.85)} fill="none" strokeLinecap="round">
      <rect x={x} y={top} width={w} height={bottom - top} rx={10} strokeWidth={3} fill={c('--brand', 0.05)} />
      <line x1={x} y1={top + 44} x2={x + w} y2={top + 44} strokeWidth={2} opacity={0.8} />
      {Array.from({ length: bars - 1 }, (_, i) => (
        <line key={i} x1={x + ((i + 1) * w) / bars} y1={top + 3} x2={x + ((i + 1) * w) / bars} y2={bottom - 3} strokeWidth={1.4} opacity={0.22} />
      ))}
      <line x1={x + 6} y1={bottom} x2={x + 6} y2={Y_GROUND + 2} strokeWidth={4} />
      <line x1={x + w - 6} y1={bottom} x2={x + w - 6} y2={Y_GROUND + 2} strokeWidth={4} />
    </g>
  );
}

function Stand({ x }: { x: number }) {
  const { url } = useSvgIds();
  const top = CARRY_CY + CARRY_R;
  return (
    <g>
      {/* back leg */}
      <rect x={x + D.dx - 3} y={top + D.dy} width={6} height={Y_GROUND - top - D.dy - 6} rx={2} fill={c('--ink-3', 0.35)} />
      {/* front leg + foot */}
      <rect x={x - 3.5} y={top} width={7} height={Y_GROUND - top} rx={2} fill={url('steel')} stroke={c('--ink-3', 0.55)} strokeWidth={1} />
      <rect x={x - 9} y={Y_GROUND - 4} width={18} height={5} rx={2} fill={c('--ink-3', 0.7)} />
      {/* cross brace */}
      <line x1={x} y1={Y_GROUND - 16} x2={x + D.dx} y2={top + 18} stroke={c('--ink-3', 0.4)} strokeWidth={2} />
    </g>
  );
}

function Drive() {
  const { url } = useSvgIds();
  // Motor + gearbox behind the head pulley (left), drawn as oblique boxes.
  const mx = 16, my = PULLEY_CY - 24, mw = 60, mh = 50;
  return (
    <g>
      <polygon points={`${mx},${my} ${mx + D.dx},${my + D.dy} ${mx + mw + D.dx},${my + D.dy} ${mx + mw},${my}`} fill={c('--ok', 0.45)} />
      <polygon points={`${mx + mw},${my} ${mx + mw + D.dx},${my + D.dy} ${mx + mw + D.dx},${my + mh + D.dy} ${mx + mw},${my + mh}`} fill={c('--ok', 0.75)} />
      <rect x={mx} y={my} width={mw} height={mh} rx={6} fill={url('motor')} stroke={c('--ok', 0.9)} strokeWidth={1} />
      {Array.from({ length: 5 }, (_, i) => (
        <line key={i} x1={mx + 8} x2={mx + mw - 8} y1={my + 9 + i * 7.5} y2={my + 9 + i * 7.5} stroke={c('--surface', 0.45)} strokeWidth={1.6} strokeLinecap="round" />
      ))}
      <rect x={mx + 4} y={my + mh} width={mw - 8} height={Y_GROUND - my - mh} fill={c('--ink-3', 0.55)} />
      <rect x={mx - 4} y={Y_GROUND - 6} width={mw + 8} height={7} rx={2} fill={c('--ink-3', 0.75)} />
      {/* coupling shaft into the pulley hub */}
      <rect x={mx + mw} y={PULLEY_CY - 4} width={X0 - mx - mw} height={8} rx={3} fill={c('--ink-2', 0.7)} />
    </g>
  );
}

function TakeUp() {
  const { url } = useSvgIds();
  const x = X1 - 58;
  const ty = Y_RET + BELT_T + 22;
  return (
    <g>
      <line x1={x + 10} y1={Y_RET + BELT_T} x2={x + 10} y2={ty} stroke={c('--ink-3', 0.7)} strokeWidth={2} />
      <line x1={x + 30} y1={Y_RET + BELT_T} x2={x + 30} y2={ty} stroke={c('--ink-3', 0.7)} strokeWidth={2} />
      <rect x={x} y={ty} width={40} height={30} rx={5} fill={url('steel')} stroke={c('--ink-3', 0.7)} strokeWidth={1.2} />
      <path d={`M ${x + 6} ${ty + 24} l 8 -18 M ${x + 16} ${ty + 24} l 8 -18 M ${x + 26} ${ty + 24} l 8 -18`} stroke={c('--warn', 0.7)} strokeWidth={3} strokeLinecap="round" />
    </g>
  );
}

const Structure = memo(function Structure({ anchorX, defectX, spin }: { anchorX: number; defectX: number | null; spin: boolean }) {
  const { url } = useSvgIds();
  const carry = useMemo(() => pitched(anchorX, CARRY_PITCH, X0 + 56, X1 - 56), [anchorX]);
  const ret = useMemo(() => pitched(defectX ?? anchorX, RETURN_PITCH, X0 + 60, X1 - 60), [defectX, anchorX]);
  const stands = carry.filter((_, i) => i % 2 === 1);
  const mat = useMemo(() => materialProfile(11), []);
  const rnd = (x: number) => Math.round(x * 10) / 10;

  return (
    <g>
      {/* backdrop: faint hills and ground haze */}
      <path d={`M 0 ${Y_TOP + 6} C 160 ${Y_TOP - 54} 320 ${Y_TOP - 30} 470 ${Y_TOP - 6} S 760 ${Y_TOP - 70} 930 ${Y_TOP - 26} S 1120 ${Y_TOP - 40} 1200 ${Y_TOP - 10} L 1200 ${Y_GROUND} L 0 ${Y_GROUND} Z`}
        fill={url('hills')} />
      <rect x={10} y={Y_GROUND - 40} width={VB.w - 20} height={56} rx={28} fill={c('--panel', 0.75)} />
      <ellipse cx={600} cy={Y_GROUND + 4} rx={560} ry={11} fill={c('--ink', 0.07)} filter={url('blur')} />

      {/* stringers */}
      <rect x={X0 + 40 + D.dx} y={CARRY_CY + CARRY_R + D.dy} width={X1 - X0 - 80} height={5} rx={2} fill={c('--ink-3', 0.3)} />

      {stands.map((x) => <Stand key={x} x={x} />)}

      <rect x={X0 + 40} y={CARRY_CY + CARRY_R} width={X1 - X0 - 80} height={6} rx={2} fill={url('steel')} stroke={c('--ink-3', 0.5)} strokeWidth={1} />

      {/* return run */}
      <polygon points={`${X0},${Y_RET} ${X0 + D.dx},${Y_RET + D.dy} ${X1 + D.dx},${Y_RET + D.dy} ${X1},${Y_RET}`} fill={c('--ink', 0.45)} />
      <rect x={X0} y={Y_RET} width={X1 - X0} height={BELT_T} fill={url('beltSide')} />
      {ret.map((x) => {
        const bad = defectX != null && Math.abs(x - defectX) < 1;
        return bad ? null : <Roller key={x} x={x} y={RETURN_CY} r={RETURN_R} />;
      })}

      <Drive />
      <TakeUp />

      {/* carry idlers */}
      {carry.map((x) => <Roller key={x} x={x} y={CARRY_CY} r={CARRY_R} />)}

      {/* top run: top face then edge */}
      <polygon points={`${X0},${Y_TOP} ${X0 + D.dx},${Y_TOP + D.dy} ${X1 + D.dx},${Y_TOP + D.dy} ${X1},${Y_TOP}`} fill={url('beltTop')} />
      <line x1={X0 + D.dx} y1={Y_TOP + D.dy + 1} x2={X1 + D.dx} y2={Y_TOP + D.dy + 1} stroke={c('--surface', 0.18)} strokeWidth={1.5} />

      {/* material */}
      <path d={mat.d} fill={url('material')} stroke={c('--surface', 0.7)} strokeWidth={1.2} strokeLinejoin="round" />
      {mat.pebbles.map((p, i) => (
        <ellipse key={i} cx={rnd(p.x)} cy={rnd(p.y)} rx={rnd(p.rx)} ry={rnd(p.ry)}
          fill={p.tone === 'brand' ? c('--brand', 0.45) : p.tone === 'light' ? c('--surface', 0.6) : c('--ink', 0.3)} />
      ))}

      <rect x={X0} y={Y_TOP} width={X1 - X0} height={BELT_T} fill={url('beltSide')} />
      <line x1={X0} y1={Y_TOP + 0.75} x2={X1} y2={Y_TOP + 0.75} stroke={c('--surface', 0.22)} strokeWidth={1.5} />

      <Guard x={X0 - 62} w={116} />
      <Guard x={X1 - 54} w={116} />
      <Pulley x={X0} side="head" spin={spin} />
      <Pulley x={X1} side="tail" spin={spin} />
    </g>
  );
});

/** Chevrons travelling along a belt edge. Spaced by `pitch` so the loop is seamless. */
function Flow({ y, dir, opacity, animate }: { y: number; dir: 1 | -1; opacity: number; animate: boolean }) {
  const { url } = useSvgIds();
  const pitch = 122;
  const xs = Array.from({ length: Math.ceil((X1 - X0) / pitch) + 2 }, (_, i) => X0 - pitch + i * pitch);
  const h = 3.4;
  const pts = (x: number) => (dir === 1 ? `${x - 3},${y - h} ${x + 2},${y} ${x - 3},${y + h}` : `${x + 3},${y - h} ${x - 2},${y} ${x + 3},${y + h}`);
  return (
    <g clipPath={url(dir === 1 ? 'clip-top' : 'clip-ret')} opacity={opacity}>
      <motion.g
        animate={animate ? { x: [0, dir * pitch] } : undefined}
        transition={animate ? { repeat: Infinity, ease: 'linear', duration: 4.2 } : undefined}
      >
        {xs.map((x) => (
          <g key={x}>
            <polyline points={pts(x)} fill="none" stroke={c('--surface', 0.9)} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
            <polyline points={pts(x + 9 * dir)} fill="none" stroke={c('--surface', 0.45)} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
          </g>
        ))}
      </motion.g>
    </g>
  );
}

/* ---------- interactive layer ---------- */

export function RouteIllustration({
  stations,
  defectStation,
  activeId,
  onHover,
  calloutTopY,
  idPrefix,
}: {
  stations: Station[];
  defectStation: Station | null;
  activeId: string | null;
  onHover: (id: string | null) => void;
  /** viewBox y where the defect callout starts (its leader ends behind it). */
  calloutTopY: number;
  /** Unique per instance (from useId) so SVG defs never collide across instances. */
  idPrefix: string;
}) {
  const reduce = !!useReducedMotion();
  const { id } = svgIds(idPrefix);
  const defectX = defectStation && isReturnStation(defectStation) ? stationX(defectStation.position) : null;
  const anchorX = stationX(stations[Math.floor(stations.length / 2)]?.position ?? 0.5);

  return (
    <SvgIdContext.Provider value={idPrefix}>
    <svg
      viewBox={`0 0 ${VB.w} ${VB.h}`}
      preserveAspectRatio="xMidYMid meet"
      className="absolute inset-0 h-full w-full overflow-visible"
      role="img"
      aria-label={`Conveyor illustration with ${stations.length} stations${defectStation ? `; defect at ${defectStation.name}` : ''}`}
    >
      <defs>
        <linearGradient id={id('beltTop')} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={c('--ink', 0.5)} />
          <stop offset="1" stopColor={c('--ink', 0.72)} />
        </linearGradient>
        <linearGradient id={id('beltSide')} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={c('--ink', 0.86)} />
          <stop offset="1" stopColor={c('--ink', 0.72)} />
        </linearGradient>
        <linearGradient id={id('roller')} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={c('--surface')} />
          <stop offset="0.5" stopColor={c('--ink-3', 0.55)} />
          <stop offset="1" stopColor={c('--ink-3', 0.9)} />
        </linearGradient>
        <linearGradient id={id('rollerBad')} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={c('--bad-bg')} />
          <stop offset="0.5" stopColor={c('--bad')} />
          <stop offset="1" stopColor={c('--bad', 0.85)} />
        </linearGradient>
        <linearGradient id={id('steel')} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={c('--surface')} />
          <stop offset="1" stopColor={c('--ink-3', 0.75)} />
        </linearGradient>
        <linearGradient id={id('motor')} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={c('--ok', 0.6)} />
          <stop offset="1" stopColor={c('--ok', 0.88)} />
        </linearGradient>
        <linearGradient id={id('material')} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={c('--line')} />
          <stop offset="0.45" stopColor={c('--ink-3')} />
          <stop offset="1" stopColor={c('--ink-2')} />
        </linearGradient>
        <linearGradient id={id('hills')} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={c('--brand', 0.06)} />
          <stop offset="1" stopColor={c('--panel', 0)} />
        </linearGradient>
        <radialGradient id={id('defectGlow')}>
          <stop offset="0" stopColor={c('--bad', 0.6)} />
          <stop offset="0.45" stopColor={c('--bad', 0.32)} />
          <stop offset="1" stopColor={c('--bad', 0)} />
        </radialGradient>
        <filter id={id('blur')} x="-10%" y="-200%" width="120%" height="500%">
          <feGaussianBlur stdDeviation="5" />
        </filter>
        <filter id={id('markerShadow')} x="-100%" y="-100%" width="300%" height="300%">
          <feDropShadow dx="0" dy="1.5" stdDeviation="1.8" floodColor={c('--ink')} floodOpacity="0.22" />
        </filter>
        <clipPath id={id('clip-top')}><rect x={X0} y={Y_TOP} width={X1 - X0} height={BELT_T} /></clipPath>
        <clipPath id={id('clip-ret')}><rect x={X0} y={Y_RET} width={X1 - X0} height={BELT_T} /></clipPath>
      </defs>

      <Structure anchorX={anchorX} defectX={defectX} spin={!reduce} />

      <Flow y={Y_TOP + BELT_T / 2} dir={1} opacity={0.9} animate={!reduce} />
      <Flow y={Y_RET + BELT_T / 2} dir={-1} opacity={0.45} animate={!reduce} />

      {/* defect roller sits above the structure so its glow reads */}
      {defectStation && (
        <DefectHalo station={defectStation} reduce={reduce} active={activeId === defectStation.id} calloutTopY={calloutTopY} />
      )}

      {/* leader lines (behind the HTML chips, which cover their top ends) */}
      {stations.map((s, i) => {
        const x = stationX(s.position);
        const bad = s.status === 'defect';
        const active = activeId === s.id;
        return (
          <motion.line
            key={s.id}
            x1={x} y1={CHIP_Y} x2={x} y2={markerY(s) - 9}
            stroke={bad ? c('--bad', 0.9) : active ? c('--ink-2') : c('--ink-3', 0.8)}
            strokeWidth={bad || active ? 2 : 1.5}
            strokeDasharray={bad ? '5 4' : undefined}
            strokeLinecap="round"
            initial={reduce ? false : { pathLength: 0, opacity: 0 }}
            animate={{ pathLength: 1, opacity: 1 }}
            transition={{ duration: 0.45, delay: reduce ? 0 : 0.15 + i * 0.06, ease: 'easeOut' }}
          />
        );
      })}

      {stations.map((s) => (
        <Marker key={s.id} station={s} active={activeId === s.id} reduce={reduce} onHover={onHover} />
      ))}
    </svg>
    </SvgIdContext.Provider>
  );
}

function DefectHalo({ station, reduce, active, calloutTopY }: { station: Station; reduce: boolean; active: boolean; calloutTopY: number }) {
  const { url } = useSvgIds();
  const x = stationX(station.position);
  const y = markerY(station);
  const onReturn = isReturnStation(station);
  return (
    <g pointerEvents="none">
      {/* leader down to the callout card */}
      <line x1={x} y1={y + 10} x2={x} y2={calloutTopY + 12} stroke={c('--bad', 0.85)} strokeWidth={2} strokeDasharray="5 4" strokeLinecap="round" />
      {reduce ? (
        <circle cx={x} cy={y} r={64} fill={url('defectGlow')} opacity={0.7} />
      ) : (
        <>
          <motion.circle
            cx={x} cy={y} r={66} fill={url('defectGlow')}
            animate={{ opacity: active ? [0.8, 1, 0.8] : [0.55, 1, 0.55] }}
            transition={{ duration: 2.2, ease: 'easeInOut', repeat: Infinity }}
          />
          {[0, 1.2].map((delay) => (
            <motion.circle
              key={delay}
              cx={x} cy={y} fill="none" stroke={c('--bad')} strokeWidth={1.5}
              initial={{ r: 12, opacity: 0.55 }}
              animate={{ r: 56, opacity: 0 }}
              transition={{ duration: 2.4, ease: 'easeOut', repeat: Infinity, delay }}
            />
          ))}
        </>
      )}
      {onReturn && <Roller x={x} y={RETURN_CY + 3} r={DEFECT_R} bad />}
    </g>
  );
}

function Marker({ station, active, reduce, onHover }: { station: Station; active: boolean; reduce: boolean; onHover: (id: string | null) => void }) {
  const { url } = useSvgIds();
  const x = stationX(station.position);
  const y = markerY(station);
  const v = statusVar[station.status];
  const bad = station.status === 'defect';
  return (
    <g
      className="cursor-pointer"
      onMouseEnter={() => onHover(station.id)}
      onMouseLeave={() => onHover(null)}
      aria-hidden
    >
      <circle cx={x} cy={y} r={24} fill="transparent" />
      <motion.g
        style={{ transformBox: 'fill-box', transformOrigin: 'center' }}
        animate={{ scale: active && !reduce ? 1.18 : 1 }}
        transition={{ type: 'spring', stiffness: 420, damping: 26 }}
      >
        {active && <circle cx={x} cy={y} r={14} fill={c(v, 0.18)} />}
        <circle cx={x} cy={y} r={bad ? 8.5 : 7.5} fill={c('--surface')} stroke={c(v)} strokeWidth={3} filter={url('markerShadow')} />
        <circle cx={x} cy={y} r={2.8} fill={c(v)} />
      </motion.g>
    </g>
  );
}
