import type { Ref } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { ChevronRight } from 'lucide-react';
import clsx from 'clsx';
import type { Defect, Station } from '../../data/types';
import { Pill } from '../ui/primitives';
import { sentenceCaseParts } from './route-geometry';

/** Tiny return-idler glyph (roller + axle) for the callout tile. */
function RollerGlyph() {
  return (
    <svg viewBox="0 0 32 20" className="h-5 w-8" aria-hidden>
      <rect x="1" y="9" width="30" height="2.4" rx="1.2" fill="rgb(var(--ink-2))" />
      <rect x="5" y="3" width="22" height="14" rx="5" fill="rgb(var(--bad))" />
      <rect x="7" y="5" width="18" height="3" rx="1.5" fill="rgb(var(--surface) / 0.35)" />
      <ellipse cx="27" cy="10" rx="2.6" ry="7" fill="rgb(var(--bad) / 0.75)" />
    </svg>
  );
}

/** Card attached under the defective station: what failed and why, with a chevron affordance. */
export function DefectCallout({
  station,
  defect,
  compact,
  active,
  onActivate,
  onDeactivate,
  onSelect,
  ref,
}: {
  station: Station;
  defect: Defect;
  compact: boolean;
  active: boolean;
  onActivate: () => void;
  onDeactivate: () => void;
  /** Opens the defect's details (the card wires this to the List view, focused on the defect row). */
  onSelect: () => void;
  ref?: Ref<HTMLButtonElement>;
}) {
  const reduce = useReducedMotion();
  const meta = station.note ? sentenceCaseParts(station.note) : defect.detail;

  return (
    <motion.button
      ref={ref}
      type="button"
      onClick={onSelect}
      onMouseEnter={onActivate}
      onMouseLeave={onDeactivate}
      onFocus={onActivate}
      onBlur={onDeactivate}
      aria-label={`Defect detected at ${[station.name, defect.asset ?? station.asset].filter(Boolean).join(' / ')}. ${meta}. Show in station list`}
      initial={reduce ? false : { opacity: 0, y: 10, x: '-50%' }}
      animate={{ opacity: 1, y: 0, x: '-50%' }}
      transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1], delay: reduce ? 0 : 0.42 }}
      className={clsx(
        'group absolute bottom-[2.5%] left-1/2 z-20 flex items-center rounded-2xl border bg-surface text-left outline-none',
        'transition-[box-shadow,border-color] duration-200 focus-visible:ring-2 focus-visible:ring-info/40',
        active ? 'border-bad/50 shadow-lift' : 'border-bad/30 shadow-card hover:shadow-lift',
        compact ? 'gap-2.5 px-2.5 py-2' : 'gap-3 px-3.5 py-2.5',
      )}
      style={{ width: compact ? 'min(52%, 300px)' : 'clamp(300px, 38%, 400px)' }}
    >
      <span className={clsx('grid shrink-0 place-items-center rounded-xl bg-bad-bg', compact ? 'h-9 w-9' : 'h-11 w-11')}>
        <RollerGlyph />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex items-center gap-1.5 whitespace-nowrap">
          <Pill tone="bad" className="!px-2 !py-0.5 !text-[11px]">Defect Detected</Pill>
          {!compact && station.vibrationDelta != null && (
            <span className="text-[11px] font-medium tabular-nums text-bad">+{station.vibrationDelta}% vs baseline</span>
          )}
          {!compact && defect.detectedAt && <span className="text-[11px] tabular-nums text-ink-3">· {defect.detectedAt}</span>}
        </span>
        <span className="truncate font-display text-[13px] font-semibold text-ink">
          {[station.name, defect.asset ?? station.asset].filter(Boolean).join(' / ')}
        </span>
        <span className={clsx('truncate text-ink-2', compact ? 'text-[11px]' : 'text-[11.5px]')}>{meta}</span>
      </span>
      <ChevronRight size={16} className="shrink-0 text-ink-3 transition-transform duration-200 group-hover:translate-x-0.5 group-hover:text-ink-2" />
    </motion.button>
  );
}
