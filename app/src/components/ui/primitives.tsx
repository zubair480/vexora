// Shared UI primitives. Owned by the integration session: build sessions use them, never edit them.
// Need something extra? Make a private helper inside your own component folder.
import clsx from 'clsx';
import type { ReactNode } from 'react';
import type { Status } from '../../data/types';

export function Card({ className, children, hover = true }: { className?: string; children: ReactNode; hover?: boolean }) {
  return <section className={clsx('card', hover && 'card-hover', className)}>{children}</section>;
}

export function CardHeader({ icon, title, subtitle, right }: { icon?: ReactNode; title: ReactNode; subtitle?: ReactNode; right?: ReactNode }) {
  return (
    <header className="flex items-start justify-between gap-3">
      <div className="flex items-start gap-2.5">
        {icon && <span className="mt-0.5 text-ink-2">{icon}</span>}
        <div>
          <h2 className="card-title">{title}</h2>
          {subtitle && <p className="label mt-0.5">{subtitle}</p>}
        </div>
      </div>
      {right}
    </header>
  );
}

export type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'sensor' | 'plum' | 'brand' | 'neutral';

const toneClass: Record<Tone, string> = {
  ok: 'bg-ok-bg text-ok',
  warn: 'bg-warn-bg text-[#B97512]',
  bad: 'bg-bad-bg text-bad',
  info: 'bg-info-bg text-info',
  sensor: 'bg-sensor-bg text-sensor',
  plum: 'bg-plum-bg text-plum',
  brand: 'bg-brand-bg text-brand',
  neutral: 'bg-panel text-ink-2',
};

export function Pill({ tone = 'neutral', dot = false, children, className }: { tone?: Tone; dot?: boolean; children: ReactNode; className?: string }) {
  return (
    <span className={clsx('inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium', toneClass[tone], className)}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

export const statusTone: Record<Status, Tone> = { ok: 'ok', attention: 'warn', defect: 'bad' };
export const statusLabel: Record<Status, string> = { ok: 'OK', attention: 'Attention', defect: 'Defect' };

/** Placeholder a build session replaces with the real section. */
export function Placeholder({ name, className }: { name: string; className?: string }) {
  return (
    <Card hover={false} className={clsx('flex min-h-[140px] items-center justify-center border-dashed', className)}>
      <span className="label">{name}: being built</span>
    </Card>
  );
}
