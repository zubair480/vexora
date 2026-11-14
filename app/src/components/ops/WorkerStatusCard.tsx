import clsx from 'clsx';
import { motion } from 'framer-motion';
import { HardHat } from 'lucide-react';
import { Card, CardHeader, Pill } from '../ui/primitives';
import { ACTION_VALUE, exposureLevel, type ExposureLevel } from '../../lib/hav';
import type { Worker } from '../../data/types';

type AvatarTone = 'info' | 'plum' | 'sensor';
const AVATAR_TONES: AvatarTone[] = ['info', 'plum', 'sensor'];
const avatarClass: Record<AvatarTone, string> = {
  info: 'bg-info-bg text-info',
  plum: 'bg-plum-bg text-plum',
  sensor: 'bg-sensor-bg text-sensor',
};

const fillClass: Record<ExposureLevel, string> = {
  below: 'bg-ok',
  action: 'bg-warn',
  limit: 'bg-bad',
};

function WorkerAvatar({ initials, tone }: { initials: string; tone: AvatarTone }) {
  return (
    <span
      className={clsx(
        'relative grid h-10 w-10 shrink-0 place-items-center rounded-full font-display text-xs font-bold',
        avatarClass[tone],
      )}
      aria-hidden="true"
    >
      {initials}
      <svg
        viewBox="0 0 14 8"
        width={14}
        height={8}
        className="absolute -top-1.5 left-1/2 -translate-x-1/2"
      >
        <path d="M3 6.2C3 3.3 4.8 1 7 1s4 2.3 4 5.2Z" fill="rgb(var(--warn))" />
        <path d="M6.3 1.2h1.4v2.6H6.3Z" fill="rgb(var(--brand))" opacity={0.55} />
        <path d="M1 6.6h12" stroke="rgb(var(--brand))" strokeWidth={1.4} strokeLinecap="round" />
      </svg>
    </span>
  );
}

function ExposureBar({ points, delay }: { points: number; delay: number }) {
  const pct = Math.min(100, (points / ACTION_VALUE) * 100);
  return (
    <div
      className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-panel"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={ACTION_VALUE}
      aria-valuenow={points}
      aria-label="Exposure against action value"
    >
      <motion.div
        className={clsx('h-full rounded-full', fillClass[exposureLevel(points)])}
        initial={{ width: 0 }}
        animate={{ width: `${pct}%` }}
        transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1], delay }}
      />
    </div>
  );
}

export function WorkerStatusCard({ workers }: { workers: Worker[] }) {
  const active = workers.filter((w) => w.onTask).length;

  return (
    <Card className="flex h-full min-h-[296px] flex-col p-5">
      <CardHeader
        icon={<HardHat size={18} strokeWidth={1.75} />}
        title={
          <>
            <span className="whitespace-nowrap">Worker Status</span>{' '}
            <span className="whitespace-nowrap font-normal text-ink-2">(On Task)</span>
          </>
        }
        right={
          <Pill tone="ok" dot className="shrink-0">
            {active} active
          </Pill>
        }
      />

      <div className="mt-4 flex flex-1 flex-col">
        {workers.length === 0 ? (
          <div className="flex flex-1 items-center justify-center rounded-xl bg-panel/60 p-3">
            <span className="label">No workers on task</span>
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {workers.map((w, i) => (
              <li key={w.id} className="flex gap-3 py-3 first:pt-1 last:pb-0">
                <WorkerAvatar initials={w.initials} tone={AVATAR_TONES[i % AVATAR_TONES.length]} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-ink">{w.name}</p>
                      <p className="truncate text-xs text-ink-2">{w.role}</p>
                    </div>
                    {w.onTask ? (
                      <Pill tone="ok" dot className="shrink-0">
                        On Task
                      </Pill>
                    ) : (
                      <Pill tone="neutral" className="shrink-0">
                        Off Task
                      </Pill>
                    )}
                  </div>
                  <div className="mt-2 flex items-baseline justify-between gap-2">
                    <span className="text-[11px] text-ink-3">Exposure</span>
                    <span className="font-display text-xs font-semibold tabular-nums text-ink">
                      {w.exposurePoints} pts{' '}
                      <span className="font-sans text-[11px] font-normal text-ink-3">of {ACTION_VALUE}</span>
                    </span>
                  </div>
                  <ExposureBar points={w.exposurePoints} delay={0.1 + i * 0.08} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}
