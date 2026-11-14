import clsx from 'clsx';
import { motion, useReducedMotion, type Variants } from 'framer-motion';
import { Activity, Gauge, HeartPulse, TriangleAlert, Users, type LucideIcon } from 'lucide-react';
import { Card, type Tone } from '../ui/primitives';
import type { Kpis } from '../../data/types';
import { beltHealthTone, defectsTone, exposureKpi, kpiToneTile, riskTone } from './kpiTone';
import { toneTextStrong } from './tone';

interface KpiItem {
  key: string;
  label: string;
  value: string;
  caption: string;
  tone: Tone;
  icon: LucideIcon;
}

const EASE = [0.22, 1, 0.36, 1] as const;

function buildItems(k: Kpis): KpiItem[] {
  const health = beltHealthTone(k.beltHealth);
  const defects = defectsTone(k.activeDefects);
  const exposure = exposureKpi(k.exposurePoints);
  return [
    { key: 'health', label: 'Belt Health', value: `${Math.round(k.beltHealth)}%`, caption: health.caption, tone: health.tone, icon: HeartPulse },
    { key: 'defects', label: 'Active Defects', value: String(k.activeDefects), caption: defects.caption, tone: defects.tone, icon: TriangleAlert },
    { key: 'workers', label: 'Workers on Task', value: String(k.workersOnTask), caption: 'On task now', tone: 'info', icon: Users },
    { key: 'risk', label: 'Vibration Risk', value: k.vibrationRisk, caption: 'vs baseline', tone: riskTone[k.vibrationRisk], icon: Activity },
    { key: 'exposure', label: 'Current Exposure', value: `${Math.round(k.exposurePoints)} pts`, caption: exposure.caption, tone: exposure.tone, icon: Gauge },
  ];
}

export function KpiRow({ kpis }: { kpis: Kpis }) {
  const reduce = useReducedMotion();
  const items = buildItems(kpis);

  const container: Variants = {
    hidden: {},
    show: { transition: { staggerChildren: reduce ? 0 : 0.06 } },
  };
  const item: Variants = {
    hidden: { opacity: 0, y: 8 },
    show: { opacity: 1, y: 0, transition: { duration: reduce ? 0 : 0.5, ease: EASE } },
  };

  return (
    <motion.ul
      aria-label="Shift key indicators"
      className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5 xl:gap-4"
      variants={container}
      initial={reduce ? false : 'hidden'}
      animate="show"
    >
      {items.map(({ key, label, value, caption, tone, icon: Icon }) => (
        <motion.li key={key} variants={item} className="min-w-0">
          <Card className="flex h-full items-center gap-2.5 p-3.5 transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5">
            <span aria-hidden className={clsx('grid h-10 w-10 shrink-0 place-items-center rounded-xl', kpiToneTile[tone])}>
              <Icon size={20} strokeWidth={2} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="label line-clamp-2 font-medium leading-snug">{label}</p>
              <p className={clsx('whitespace-nowrap font-display text-xl font-bold leading-tight tabular-nums 2xl:text-2xl', toneTextStrong[tone])}>{value}</p>
              <p className="line-clamp-2 text-[11px] leading-snug text-ink-2">{caption}</p>
            </div>
          </Card>
        </motion.li>
      ))}
    </motion.ul>
  );
}
