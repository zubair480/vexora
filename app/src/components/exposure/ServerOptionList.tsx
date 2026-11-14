// Renders the planner's control options exactly as the server sent them (see serverOptions.ts).
// A number is shown only when the server gave one; nothing is computed or filled in here.
import { motion, type Transition } from 'framer-motion';
import { ClipboardList } from 'lucide-react';
import clsx from 'clsx';
import { Pill } from '../ui/primitives';
import { exposureLevel } from '../../lib/hav';
import type { ServerOption } from './serverOptions';
import { levelLabel, levelTone, toneTextStrong } from './tone';

export function ServerOptionList({
  options,
  reduce,
  transition,
}: {
  options: ServerOption[];
  reduce: boolean;
  transition: (duration: number, delay?: number) => Transition;
}) {
  if (!options.length) {
    return <p className="mt-2 text-sm text-ink-2">The planner has not listed any control options for this task yet.</p>;
  }
  return (
    <ul className="mt-2 space-y-2">
      {options.map((o, i) => {
        const total = o.projectedPoints;
        const lvl = total == null ? null : exposureLevel(total);
        return (
          <motion.li
            key={o.key}
            initial={reduce ? false : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={transition(0.45, 0.08 + i * 0.06)}
            className="flex items-start gap-3 rounded-xl border border-line bg-surface px-3 py-2.5 transition-colors duration-200 hover:border-ink-3/30 hover:bg-panel/60"
          >
            <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-panel text-ink-2">
              <ClipboardList size={16} aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-sm font-medium leading-snug text-ink">
                {o.title}
                {o.requiresApproval && (
                  <Pill tone="neutral" className="px-2 py-0.5 text-[10px] leading-none">
                    Needs approval
                  </Pill>
                )}
              </p>
              {o.detail && <p className="label mt-0.5 leading-snug">{o.detail}</p>}
              {(o.additionalPoints != null || total != null) && (
                <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <p className="text-xs tabular-nums text-ink-2">
                    {o.additionalPoints != null && <>+{Math.round(o.additionalPoints)} pts </>}
                    {total != null && (
                      <>
                        <span className="text-ink-3" aria-hidden="true">→</span>
                        <span className="sr-only">for a total of</span>{' '}
                        <span className={clsx('font-display text-sm font-semibold', lvl && toneTextStrong[levelTone[lvl]])}>
                          {Math.round(total)} pts
                        </span>
                      </>
                    )}
                  </p>
                  {lvl && (
                    <Pill tone={levelTone[lvl]} dot className="py-0.5 text-[11px]">
                      {levelLabel[lvl]}
                    </Pill>
                  )}
                </div>
              )}
            </div>
          </motion.li>
        );
      })}
    </ul>
  );
}
