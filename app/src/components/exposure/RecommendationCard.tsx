// "AI insight" recommendation card: headline, idler thumbnail, before → after exposure chip,
// and a "Review & Plan" panel comparing three HAV controls (computed with lib/hav).
import { useId, useMemo, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ArrowRight, ChevronDown, Repeat, Sparkles, Users, Wrench, type LucideIcon } from 'lucide-react';
import clsx from 'clsx';
import { Card, Pill } from '../ui/primitives';
import { exposureLevel, ACTION_VALUE } from '../../lib/hav';
import { shift } from '../../data/shift';
import type { Exposure, Recommendation } from '../../data/types';
import { FOCUS_RING, levelLabel, levelTone, toneTextStrong } from './tone';
import { IdlerIllustration } from './IdlerIllustration';
import { buildControlOptions, type ControlId } from './controlOptions';

const EASE = [0.22, 1, 0.36, 1] as const;
const FOCUS = FOCUS_RING;

const optionIcon: Record<ControlId, LucideIcon> = {
  'low-vib-tool': Wrench,
  rotation: Repeat,
  split: Users,
};

export function RecommendationCard({ recommendation, exposure }: { recommendation: Recommendation; exposure: Exposure }) {
  const reduce = useReducedMotion() ?? false;
  const [open, setOpen] = useState(false);
  const [showDetail, setShowDetail] = useState(false);
  const uid = useId();
  const planId = `rec-plan-${uid}`;
  const detailId = `rec-detail-${uid}`;

  const { current } = exposure;
  const { projectedPoints, deltaPoints } = recommendation;
  const curLevel = exposureLevel(current);
  const projLevel = exposureLevel(projectedPoints);

  const options = useMemo(
    () => buildControlOptions(exposure, recommendation, shift.tool, shift.workers.filter((w) => w.onTask).length),
    [exposure, recommendation],
  );

  const t = (duration: number, delay = 0) => (reduce ? { duration: 0 } : { duration, delay, ease: EASE });

  return (
    <Card className="relative shrink-0 overflow-hidden p-5">
      {/* faint brand wash in the top corner */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 bg-gradient-to-br from-brand-bg/60 via-transparent to-transparent"
      />

      <div className="relative">
        {/* Header */}
        <div className="flex items-center gap-3">
          <motion.span
            className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-bg text-brand"
            initial={reduce ? false : { scale: 0.9, opacity: 0.6 }}
            animate={{ scale: [0.9, 1.06, 1], opacity: 1 }}
            transition={reduce ? { duration: 0 } : { duration: 0.7, ease: EASE, times: [0, 0.55, 1] }}
          >
            <Sparkles size={20} strokeWidth={2} aria-hidden="true" />
          </motion.span>
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h2 className="label font-semibold uppercase tracking-wide text-ink">Recommendation</h2>
            {/* Local chip instead of Pill: the shared brand pill text is below 4.5:1 on its tint. */}
            <span
              className={clsx(
                'inline-flex items-center rounded-full bg-brand-bg px-2.5 py-1 text-xs font-medium',
                toneTextStrong.brand,
              )}
            >
              AI insight
            </span>
          </div>
        </div>

        {/* Body */}
        <div className="mt-4 grid grid-cols-[1fr_auto] gap-3 2xl:gap-4">
          <div className="min-w-0">
            <p className="font-display text-[17px] font-semibold leading-snug text-ink">{recommendation.headline}</p>
            <p
              id={detailId}
              className={clsx('mt-2 text-sm leading-relaxed text-ink-2', !showDetail && 'line-clamp-4')}
            >
              {recommendation.detail}
            </p>
          </div>
          <div className="grid h-16 w-16 place-items-center self-start rounded-2xl bg-panel 2xl:h-[88px] 2xl:w-[88px]">
            <IdlerIllustration size={60} className="2xl:h-20 2xl:w-20" />
          </div>
        </div>

        {/* Before → after exposure chip */}
        <div
          className="mt-4 inline-flex flex-wrap items-center gap-2 rounded-full border border-line bg-surface px-3 py-1.5 text-sm shadow-card"
          aria-label={`Exposure ${current} points now, ${projectedPoints} points after the next task, up ${deltaPoints}. ${levelLabel[projLevel]}.`}
          role="group"
        >
          <span className="label hidden sm:inline">Exposure</span>
          <span className={clsx('font-display font-semibold tabular-nums', toneTextStrong[levelTone[curLevel]])}>
            {current}
          </span>
          <ArrowRight size={14} className="text-ink-3" aria-hidden="true" />
          <span className={clsx('font-display font-semibold tabular-nums', toneTextStrong[levelTone[projLevel]])}>
            {projectedPoints} pts
          </span>
          <Pill tone="bad" className="py-0.5 tabular-nums">+{deltaPoints}</Pill>
        </div>

        {/* CTAs */}
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-controls={planId}
            className={clsx(
              'inline-flex items-center gap-1.5 rounded-xl bg-brand px-4 py-2.5 text-sm font-semibold text-ink shadow-card transition duration-200 hover:bg-brand/90 hover:shadow-lift active:translate-y-px',
              FOCUS,
            )}
          >
            {recommendation.primaryCta}
            <ChevronDown
              size={16}
              aria-hidden="true"
              className={clsx('transition-transform duration-300', open && 'rotate-180')}
            />
          </button>
          <button
            type="button"
            onClick={() => setShowDetail((s) => !s)}
            aria-expanded={showDetail}
            aria-controls={detailId}
            className={clsx(
              'rounded-xl border border-line bg-surface px-4 py-2.5 text-sm font-medium text-ink transition duration-200 hover:bg-panel active:translate-y-px',
              FOCUS,
            )}
          >
            {showDetail ? 'Hide Details' : recommendation.secondaryCta}
          </button>
        </div>

        {/* Plan panel */}
        {/* Permanent wrapper so aria-controls always resolves, even while the panel is closed. */}
        <div id={planId}>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              key="plan"
              role="region"
              aria-label="Control options for the next task"
              className="overflow-hidden"
              initial={reduce ? false : { height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={reduce ? { height: 0, opacity: 0, transition: { duration: 0 } } : { height: 0, opacity: 0 }}
              transition={t(0.5)}
            >
              <div className="mt-4">
                <div className="flex items-baseline justify-between gap-2">
                  <p className="label font-semibold uppercase tracking-wide">Control options</p>
                  <p className="text-[11px] text-ink-2">Action value {ACTION_VALUE} pts</p>
                </div>
                <ul className="mt-2 space-y-2">
                  {options.map((o, i) => {
                    const Icon = optionIcon[o.id];
                    const tone = levelTone[o.level];
                    return (
                      <motion.li
                        key={o.id}
                        initial={reduce ? false : { opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={t(0.45, 0.08 + i * 0.06)}
                        className="flex items-start gap-3 rounded-xl border border-line bg-surface px-3 py-2.5 transition-colors duration-200 hover:border-ink-3/30 hover:bg-panel/60"
                      >
                        <span
                          className={clsx(
                            'mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg',
                            o.best ? 'bg-ok-bg text-ok' : 'bg-panel text-ink-2',
                          )}
                        >
                          <Icon size={16} aria-hidden="true" />
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-sm font-medium leading-snug text-ink">
                            {o.title}
                            {o.best && <Pill tone="ok" className="px-2 py-0.5 text-[10px] leading-none">Best</Pill>}
                          </p>
                          <p className="label mt-0.5 leading-snug">{o.detail}</p>
                          <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                            <p className="text-xs tabular-nums text-ink-2">
                              +{o.optionPts} pts <span className="text-ink-3" aria-hidden="true">→</span>
                              <span className="sr-only">for a total of</span>{' '}
                              <span className={clsx('font-display text-sm font-semibold', toneTextStrong[tone])}>{o.total} pts</span>
                            </p>
                            <Pill tone={tone} dot className="py-0.5 text-[11px]">
                              {levelLabel[o.level]}
                            </Pill>
                          </div>
                        </div>
                      </motion.li>
                    );
                  })}
                </ul>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        </div>
      </div>
    </Card>
  );
}
