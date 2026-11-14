import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { animate, motion, useMotionValue, useReducedMotion, useTransform } from 'framer-motion';
import { Info } from 'lucide-react';
import clsx from 'clsx';
import { Card, CardHeader, Pill } from '../ui/primitives';
import { exposureLevel } from '../../lib/hav';
import type { Exposure } from '../../data/types';
import { GAUGE, arcPath, gaugeMax, gaugeStroke, makeFrac, polar } from './gaugeScale';
import { FOCUS_RING, levelLabel, levelTone, toneTextStrong } from './tone';

const EASE = [0.22, 1, 0.36, 1] as const;
const FOCUS = FOCUS_RING;

export function ExposureGauge({ exposure }: { exposure: Exposure }) {
  const { current, projected, action, limit, label, reasons } = exposure;
  // Server data never invents a value: a null current or projection is shown as "—", never filled in.
  const lvl = current == null ? null : exposureLevel(current);
  const projLvl = projected == null ? null : exposureLevel(projected);
  const pct = current == null ? null : Math.round((current / action) * 100);
  const reason = current == null ? reasons?.find(Boolean) : undefined;

  return (
    <Card className="h-full p-5">
      <CardHeader
        title="Estimated HAV Points"
        right={
          <div className="flex shrink-0 items-center gap-2">
            <InfoTip label="About HAV points">
              UK HSE points: points = 2 × a<sub>hv</sub>² × hours. {action} = action value, {limit} = limit value.
            </InfoTip>
            {lvl ? (
              <Pill tone={levelTone[lvl]} dot className="shrink-0">
                {levelLabel[lvl]}
              </Pill>
            ) : (
              <Pill tone="neutral" className="shrink-0">
                Not available
              </Pill>
            )}
          </div>
        }
      />

      {/* Stacked inside the narrow xl column; side by side only where the card is wide enough. */}
      <div className="mt-4 flex flex-col items-center gap-4 sm:flex-row xl:flex-col min-[1700px]:flex-row">
        <div className="flex w-full max-w-[220px] shrink-0 flex-col items-center">
          <GaugeSvg exposure={exposure} />
          {pct != null ? (
            <p className="-mt-1 text-sm text-ink-2">
              <span className="font-display font-semibold tabular-nums text-ink">{pct}%</span> of action value
            </p>
          ) : (
            reason && <p className="-mt-1 text-center text-xs leading-snug text-ink-2">{reason}</p>
          )}
          {label && <p className="mt-1.5 text-center text-[11px] leading-snug text-ink-2">{label}</p>}
        </div>

        <ul className="grid w-full flex-1 grid-cols-2 gap-2 sm:block sm:space-y-2 xl:grid xl:space-y-0 min-[1700px]:block min-[1700px]:space-y-2">
          <li className="rounded-xl bg-panel px-3 py-2">
            <p className="label">Current Exposure</p>
            <p className="font-display font-semibold tabular-nums text-ink">{current == null ? '—' : `${Math.round(current)} points`}</p>
          </li>
          <li className={clsx('rounded-xl px-3 py-2', projected == null ? 'bg-panel' : 'bg-bad-bg')}>
            <p className="label">Next-Task (Projected)</p>
            {projected == null ? (
              <p className="font-display font-semibold text-ink">{'—'}</p>
            ) : (
              <p className={clsx('flex items-baseline gap-1.5 font-display font-semibold tabular-nums', toneTextStrong.bad)}>
                {Math.round(projected)} points
                {current != null && <span className="text-xs font-medium">+{Math.round(projected - current)}</span>}
              </p>
            )}
            {projLvl && projLvl !== lvl && <p className={clsx('text-[11px] font-medium', toneTextStrong.bad)}>{levelLabel[projLvl]}</p>}
          </li>
          <LegendRow dot="bg-warn" name="Action value" value={action} />
          <LegendRow dot="bg-bad" name="Limit value" value={limit} />
        </ul>
      </div>
    </Card>
  );
}

function LegendRow({ dot, name, value }: { dot: string; name: string; value: number }) {
  return (
    <li className="flex items-center gap-2.5 px-3 py-1">
      <span className={clsx('h-2 w-2 shrink-0 rounded-full', dot)} aria-hidden />
      <span className="text-xs text-ink-2">
        {name} · <span className="font-display font-semibold tabular-nums text-ink">{value} points</span>
      </span>
    </li>
  );
}

function GaugeSvg({ exposure }: { exposure: Exposure }) {
  const { current, projected, action, limit } = exposure;
  const reduce = useReducedMotion() ?? false;
  const hasCurrent = current != null;
  const lvl = hasCurrent ? exposureLevel(current) : 'below';
  // The projected ghost needs both real values; with either missing it is simply not drawn.
  const showProj = current != null && projected != null;

  const max = gaugeMax(action, limit, projected ?? 0);
  const frac = makeFrac(action, max);
  const fCur = frac(current ?? 0);
  const fProj = frac(showProj ? Math.max(projected, current) : current ?? 0);
  const fAction = frac(action);
  const fLimit = frac(limit);

  // One progress value (0→1) drives the arc, the knob position and the count-up, so they stay in sync.
  const prog = useMotionValue(reduce ? 1 : 0);
  useEffect(() => {
    if (reduce) {
      prog.set(1);
      return;
    }
    prog.set(0);
    const ctl = animate(prog, 1, { duration: 0.9, ease: EASE, delay: 0.1 });
    return () => ctl.stop();
  }, [reduce, current, prog]);

  const knobX = useTransform(prog, (p) => polar(fCur * p).x);
  const knobY = useTransform(prog, (p) => polar(fCur * p).y);
  const count = useTransform(prog, (p) => (current == null ? '—' : String(Math.round(current * p))));
  const projPt = polar(fProj);

  const ticks = [
    { v: 0, f: 0 },
    { v: action, f: fAction },
    { v: limit, f: fLimit },
  ];

  const aria =
    (current == null
      ? 'Estimated exposure not available. '
      : `Estimated exposure ${Math.round(current)} points, ${Math.round((current / action) * 100)}% of action value. `) +
    (projected == null ? '' : `Projected ${Math.round(projected)} points after next task. `) +
    `Action value ${action}, limit value ${limit}.`;

  return (
    <svg viewBox="0 0 240 150" className="block h-auto w-full overflow-visible" role="img" aria-label={aria}>
      {/* 1. Band track: green below action, amber to limit, red beyond. */}
      <g fill="none" strokeWidth={GAUGE.stroke} strokeLinecap="round">
        <path d={arcPath(fLimit, 1)} className="stroke-bad/25" />
        <path d={arcPath(fAction, fLimit)} className="stroke-warn/25" />
        <path d={arcPath(0, fAction)} className="stroke-ok/20" />
      </g>

      {/* 5. Ticks at 0 / action / limit, labels outside the arc. */}
      <g aria-hidden>
        {ticks.map((t) => {
          const a = polar(t.f, GAUGE.r + GAUGE.stroke / 2 + 2);
          const b = polar(t.f, GAUGE.r + GAUGE.stroke / 2 + 6);
          const l = polar(t.f, GAUGE.r + GAUGE.stroke / 2 + 13);
          return (
            <g key={t.v}>
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="stroke-ink-3" strokeWidth={1.25} strokeLinecap="round" />
              <text x={l.x} y={l.y} textAnchor="middle" dominantBaseline="central" className="fill-ink-2 text-[10px] tabular-nums">
                {t.v}
              </text>
            </g>
          );
        })}
      </g>

      {/* 3. Projected ghost arc: dashed, fades in once the current arc has landed. */}
      {showProj && (
      <motion.g
        initial={reduce ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: reduce ? 0 : 0.5, delay: reduce ? 0 : 0.7, ease: EASE }}
        aria-hidden
      >
        <path
          d={arcPath(fCur, fProj)}
          fill="none"
          strokeWidth={GAUGE.stroke - 6}
          strokeDasharray="4 5"
          className="stroke-bad/45"
        />
        <circle cx={projPt.x} cy={projPt.y} r={5} strokeWidth={2.5} className="fill-surface stroke-bad" />
      </motion.g>
      )}

      {/* 2. Progress arc 0 → current, and 4. its knob: only when there is a real current value. */}
      {hasCurrent && (
      <>
      <motion.path
        d={arcPath(0, fCur)}
        fill="none"
        strokeWidth={GAUGE.stroke}
        strokeLinecap="round"
        className={gaugeStroke[lvl]}
        style={{ pathLength: prog }}
      />

      {/* 4. Knob riding the end of the progress arc. */}
      <motion.circle
        cx={knobX}
        cy={knobY}
        r={7}
        strokeWidth={3.5}
        className={clsx('fill-surface drop-shadow-sm', gaugeStroke[lvl])}
      />
      </>
      )}

      {/* Centre readout. */}
      <motion.text
        x={GAUGE.cx}
        y={GAUGE.cy - 22}
        textAnchor="middle"
        className={clsx('font-display text-[44px] font-bold tabular-nums', hasCurrent ? 'fill-ink' : 'fill-ink-3')}
        aria-hidden
      >
        {count}
      </motion.text>
      <text x={GAUGE.cx} y={GAUGE.cy - 2} textAnchor="middle" className="fill-ink-2 text-xs" aria-hidden>
        points
      </text>
    </svg>
  );
}

function InfoTip({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const closeTimer = useRef<number | undefined>(undefined);

  const show = () => {
    window.clearTimeout(closeTimer.current);
    setOpen(true);
  };
  // Short grace period so the pointer can travel from the trigger onto the tooltip (WCAG 1.4.13).
  const hide = () => {
    window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setOpen(false), 150);
  };

  useEffect(() => () => window.clearTimeout(closeTimer.current), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <span className="relative inline-flex" onMouseEnter={show} onMouseLeave={hide}>
      <button
        type="button"
        aria-label={label}
        aria-describedby={id}
        onFocus={show}
        onBlur={() => setOpen(false)}
        className={clsx('grid h-6 w-6 place-items-center rounded-full text-ink-2 transition-colors hover:text-ink', FOCUS)}
      >
        <Info size={14} aria-hidden />
      </button>
      {/* pt-2 is a transparent bridge inside the hover box, so there is no gap that fires mouseleave. */}
      <span
        id={id}
        role="tooltip"
        className={clsx(
          'absolute right-0 top-full z-20 w-64 pt-2 transition-opacity duration-150',
          open ? 'opacity-100' : 'pointer-events-none opacity-0',
        )}
      >
        <span className="block rounded-xl border border-line bg-surface p-3 text-left text-xs font-normal leading-relaxed text-ink-2 shadow-lift">
          {children}
        </span>
      </span>
    </span>
  );
}
