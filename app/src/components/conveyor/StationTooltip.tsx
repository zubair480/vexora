import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import clsx from 'clsx';
import type { Station } from '../../data/types';
import { statusLabel } from '../ui/primitives';

const dotClass = { ok: 'bg-ok', attention: 'bg-warn', defect: 'bg-bad' } as const;

/** Dark hover card that opens beside a station chip (away from the card edge nearest it). */
export function StationTooltip({
  id,
  station,
  open,
  side,
  distanceM,
  showRole,
}: {
  id: string;
  station: Station;
  open: boolean;
  side: 'left' | 'right';
  distanceM: number;
  showRole: boolean;
}) {
  const reduce = useReducedMotion();
  return (
    <div
      className={clsx(
        'pointer-events-none absolute top-1/2 z-30 -translate-y-1/2',
        side === 'right' ? 'left-full ml-2.5' : 'right-full mr-2.5',
      )}
    >
    <AnimatePresence>
      {open && (
        <motion.div
          id={id}
          role="tooltip"
          initial={reduce ? { opacity: 0 } : { opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, y: 4 }}
          transition={{ duration: 0.14, ease: 'easeOut' }}
          className="w-max max-w-[220px] whitespace-normal rounded-lg bg-ink px-2.5 py-2 text-left text-xs text-surface shadow-lift"
        >
          <div className="flex items-center gap-1.5">
            <span className={clsx('h-1.5 w-1.5 shrink-0 rounded-full', dotClass[station.status])} />
            <span className="font-semibold">{station.asset ?? station.name}</span>
            <span className="text-surface/50">· {statusLabel[station.status]}</span>
          </div>
          {showRole && <div className="mt-0.5 text-surface/70">{station.name} · {station.role}</div>}
          {station.note && <div className="mt-1 leading-snug text-surface/85">{station.note}</div>}
          {/* Compact drops the distance so the card stays clear of the belt; vibration still shows. */}
          {(!showRole || station.vibrationDelta != null) && (
            <div className="mt-1.5 border-t border-surface/10 pt-1.5 tabular-nums text-surface/55">
              {!showRole && <>≈ {distanceM.toLocaleString('en-US')} m from head</>}
              {!showRole && station.vibrationDelta != null && ' · '}
              {station.vibrationDelta != null && <span className="text-bad-bg">+{station.vibrationDelta}% vibration</span>}
            </div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
    </div>
  );
}
