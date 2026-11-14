import { Sun, Truck } from 'lucide-react';
import { Pill } from '../components/ui/primitives';
import type { Conveyor, Shift } from '../data/types';

export function HeroHeader({ conveyor, shift }: { conveyor: Conveyor; shift: Shift }) {
  return (
    <div className="relative overflow-hidden">
      {/* soft mine silhouette behind the title */}
      <svg className="pointer-events-none absolute -right-10 -top-6 h-40 w-[620px] opacity-70" viewBox="0 0 620 160" aria-hidden>
        <path d="M0 150 L120 70 L190 110 L300 30 L410 100 L480 60 L620 130 L620 160 L0 160Z" fill="rgb(var(--brand-bg))" />
        <path d="M60 160 L200 95 L280 125 L380 70 L520 140 L620 110 L620 160Z" fill="rgb(var(--panel))" />
      </svg>
      <div className="relative flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-[34px] font-bold leading-tight tracking-tight">
            Conveyor Maintenance &amp; Vibration Safety
          </h1>
          <p className="mt-1 text-[15px] text-ink-2">Belt health, vibration risk, and worker exposure in one view.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Pill tone="neutral"><Truck size={14} /> {conveyor.id}{conveyor.name ? ` · ${conveyor.name}` : ''}</Pill>
          {conveyor.inService !== null && (
            <Pill tone={conveyor.inService ? 'ok' : 'warn'} dot>{conveyor.inService ? 'In Service' : 'Out of Service'}</Pill>
          )}
          {shift.label && (
            <Pill tone="brand">
              <Sun size={14} /> Shift: {shift.label}
              {shift.start && shift.end ? ` (${shift.start} – ${shift.end})` : ''}
            </Pill>
          )}
        </div>
      </div>
    </div>
  );
}
