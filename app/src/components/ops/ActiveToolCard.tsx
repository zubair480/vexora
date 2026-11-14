import type { ReactNode } from 'react';
import { Wrench } from 'lucide-react';
import { Card, CardHeader, Pill } from '../ui/primitives';
import { havPoints } from '../../lib/hav';
import type { Tool } from '../../data/types';
import { ImpactWrenchArt } from './ActiveToolIllustration';

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2 py-2 text-sm">
      <dt className="min-w-0 leading-snug text-ink-2">{label}</dt>
      <dd className="text-right font-medium tabular-nums text-ink">{children}</dd>
    </div>
  );
}

export function ActiveToolCard({ tool }: { tool: Tool }) {
  const points = Math.round(havPoints(tool.aHv, tool.minutesToday));

  return (
    <Card className="flex h-full min-h-[296px] flex-col p-5">
      <CardHeader
        icon={<Wrench size={18} strokeWidth={1.75} />}
        title="Active Tool"
        right={
          tool.inUse ? (
            <Pill tone="ok" dot className="shrink-0">
              In Use
            </Pill>
          ) : (
            <Pill tone="neutral" className="shrink-0">
              Idle
            </Pill>
          )
        }
      />

      <div className="mt-4 flex flex-1 flex-col">
        <p className="truncate font-display text-base font-semibold text-ink" title={tool.name}>
          {tool.name}
        </p>

        <div className="mt-3 flex flex-1 flex-wrap items-start gap-4">
          <div className="grid h-24 w-24 shrink-0 place-items-center rounded-2xl bg-warn-bg">
            <ImpactWrenchArt active={tool.inUse} className="h-20 w-20" />
          </div>

          <dl className="min-w-[136px] flex-1 divide-y divide-line">
            <Row label="Tool type">{tool.type}</Row>
            <Row
              label={
                <>
                  HAV emission (a<sub>hv</sub>)
                </>
              }
            >
              {tool.aHv} m/s²
            </Row>
            <Row label="Usage today">{tool.minutesToday} min</Row>
            <div className="flex items-center justify-between gap-2 py-2 text-sm">
              <dt className="min-w-0 leading-snug text-ink-2">
                Points added
                <span className="block text-[11px] text-ink-3">Estimated</span>
              </dt>
              <dd className="text-right font-display font-semibold tabular-nums text-brand">+{points} pts</dd>
            </div>
          </dl>
        </div>
      </div>
    </Card>
  );
}
