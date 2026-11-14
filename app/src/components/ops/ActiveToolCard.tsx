import type { ReactNode } from 'react';
import { Wrench } from 'lucide-react';
import { Card, CardHeader, Pill, type Tone } from '../ui/primitives';
import { havPoints } from '../../lib/hav';
import type { Tool } from '../../data/types';
import { ImpactWrenchArt } from './ActiveToolIllustration';
import { DASH, OpsEmptyCard } from './OpsEmpty';

const CONFIRMATION: Record<string, Tone> = {
  confirmed: 'ok',
  unconfirmed: 'warn',
  rejected: 'neutral',
};

function titleCase(v: string) {
  return v.charAt(0).toUpperCase() + v.slice(1).replace(/[_-]/g, ' ');
}

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2 py-2 text-sm">
      <dt className="min-w-0 leading-snug text-ink-2">{label}</dt>
      <dd className="whitespace-nowrap text-right font-medium tabular-nums text-ink">{children}</dd>
    </div>
  );
}

const ICON = <Wrench size={18} strokeWidth={1.75} />;

export function ActiveToolCard({ tool }: { tool: Tool | null }) {
  if (!tool) {
    return <OpsEmptyCard icon={ICON} title="Active Tool" message="No tool in use this shift" minH="min-h-[296px]" />;
  }

  // Points only when the emission is actually known; never estimate from a missing a_hv.
  const points = tool.aHv !== null ? Math.round(havPoints(tool.aHv, tool.minutesToday)) : null;
  const confirmTone: Tone | null = tool.confirmation ? CONFIRMATION[tool.confirmation] ?? 'neutral' : null;

  return (
    <Card className="flex h-full min-h-[296px] flex-col p-5">
      <CardHeader
        icon={ICON}
        title="Active Tool"
        right={
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            {tool.confirmation && confirmTone && (
              <Pill tone={confirmTone} className="shrink-0">
                {titleCase(tool.confirmation)}
              </Pill>
            )}
            {tool.inUse ? (
              <Pill tone="ok" dot className="shrink-0">
                In Use
              </Pill>
            ) : (
              <Pill tone="neutral" className="shrink-0">
                Idle
              </Pill>
            )}
          </div>
        }
      />

      <div className="mt-4 flex flex-1 flex-col">
        <p className="truncate font-display text-base font-semibold text-ink" title={tool.name}>
          {tool.name}
        </p>

        <div className="mt-3 flex flex-1 flex-wrap items-start gap-3">
          <div className="grid h-20 w-20 shrink-0 place-items-center rounded-2xl bg-warn-bg 2xl:h-24 2xl:w-24">
            <ImpactWrenchArt active={tool.inUse} className="h-16 w-16 2xl:h-20 2xl:w-20" />
          </div>

          <dl className="min-w-[136px] flex-1 divide-y divide-line">
            <Row label="Tool type">{tool.type ?? DASH}</Row>
            <div className="py-2 text-sm">
              <div className="flex items-center justify-between gap-2">
                <dt className="min-w-0 leading-snug text-ink-2">
                  Emission (a<sub>hv</sub>)
                </dt>
                <dd className="whitespace-nowrap text-right font-medium tabular-nums text-ink">
                  {tool.aHv !== null ? `${tool.aHv} m/s²` : DASH}
                </dd>
              </div>
              {tool.aHvSource && <p className="mt-0.5 text-[11px] leading-snug text-ink-3">Source: {tool.aHvSource}</p>}
            </div>
            <Row label="Usage today">{tool.minutesToday} min</Row>
            {points !== null && (
              <div className="flex items-center justify-between gap-2 py-2 text-sm">
                <dt className="min-w-0 leading-snug text-ink-2">
                  Points added
                  <span className="block text-[11px] text-ink-3">Estimated</span>
                </dt>
                <dd className="whitespace-nowrap text-right font-display font-semibold tabular-nums text-brand">+{points} pts</dd>
              </div>
            )}
          </dl>
        </div>
      </div>
    </Card>
  );
}
