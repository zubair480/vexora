import { motion, useReducedMotion } from 'framer-motion';
import { CircleAlert, CircleCheck, TriangleAlert, type LucideIcon } from 'lucide-react';
import clsx from 'clsx';
import type { Station, Status } from '../../data/types';
import { statusLabel } from '../ui/primitives';
import { StationTooltip } from './StationTooltip';

const statusIcon: Record<Status, { Icon: LucideIcon; fg: string; bg: string }> = {
  ok: { Icon: CircleCheck, fg: 'text-ok', bg: 'bg-ok-bg' },
  attention: { Icon: CircleAlert, fg: 'text-warn', bg: 'bg-warn-bg' },
  defect: { Icon: TriangleAlert, fg: 'text-bad', bg: 'bg-bad-bg' },
};

export type ChipDensity = 'full' | 'compact' | 'icon';

/** Label chip for one station. It is the keyboard path to the station tooltip. */
export function StationChip({
  station,
  xPct,
  yPct,
  order,
  active,
  density,
  distanceM,
  tipId,
  onActivate,
  onDeactivate,
}: {
  station: Station;
  xPct: number;
  yPct: number;
  order: number;
  active: boolean;
  density: ChipDensity;
  distanceM: number;
  /** Unique tooltip id (instance-scoped by RouteView). */
  tipId: string;
  onActivate: () => void;
  onDeactivate: () => void;
}) {
  const reduce = useReducedMotion();
  const { Icon, fg, bg } = statusIcon[station.status];
  const defect = station.status === 'defect';
  // Keep edge chips inside the card: nudge the anchor inward instead of centring.
  const shift = xPct < 12 ? '-30%' : xPct > 88 ? '-70%' : '-50%';
  const iconSize = density === 'full' ? 22 : 18;

  return (
    <div
      className={clsx('absolute', active ? 'z-30' : 'z-10')}
      style={{ left: `${xPct}%`, top: `${yPct}%`, transform: `translate(${shift}, -50%)` }}
    >
      <motion.button
        type="button"
        aria-describedby={active ? tipId : undefined}
        aria-label={`${station.name}, ${station.role}, ${statusLabel[station.status]}`}
        onMouseEnter={onActivate}
        onMouseLeave={onDeactivate}
        onFocus={onActivate}
        // Touch: iOS Safari does not focus buttons on tap, so a tap opens the tooltip explicitly.
        // (Not a toggle: the emulated mouseenter fires first and a toggle would close it again.)
        onClick={onActivate}
        onBlur={onDeactivate}
        initial={reduce ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: active && !reduce ? -2 : 0 }}
        transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1], delay: reduce ? 0 : 0.08 + order * 0.06, y: { duration: 0.18 } }}
        className={clsx(
          'flex items-center whitespace-nowrap rounded-xl border text-left outline-none transition-[box-shadow,border-color] duration-200',
          'focus-visible:ring-2 focus-visible:ring-info/40',
          density === 'full' ? 'gap-2 px-2.5 py-1.5' : density === 'compact' ? 'gap-1.5 px-1.5 py-1' : 'p-1',
          defect ? clsx('border-bad/40 bg-bad-bg ring-bad/10', density === 'full' ? 'ring-4' : 'ring-2') : 'border-line bg-surface',
          active ? 'shadow-lift' : 'shadow-card',
        )}
      >
        <span
          className={clsx('relative grid shrink-0 place-items-center rounded-full', bg, fg)}
          style={{ width: iconSize + 4, height: iconSize + 4 }}
        >
          {defect && !reduce && <span className="absolute inset-0 animate-ping rounded-full bg-bad/25" />}
          <Icon size={iconSize - 6} strokeWidth={2.4} className="relative" />
        </span>
        {density !== 'icon' && (
          <span className="flex flex-col leading-tight">
            <span className={clsx('font-semibold', density === 'full' ? 'text-[12.5px]' : 'text-[11.5px]', defect ? 'text-bad' : 'text-ink')}>
              {station.name}
            </span>
            <span className={clsx('text-ink-2', density === 'full' ? 'text-[11px]' : 'text-[10px]')}>{station.role}</span>
          </span>
        )}
      </motion.button>
      <StationTooltip
        id={tipId}
        station={station}
        open={active}
        side={xPct < 50 ? 'right' : 'left'}
        distanceM={distanceM}
        showRole={density === 'icon'}
      />
    </div>
  );
}
