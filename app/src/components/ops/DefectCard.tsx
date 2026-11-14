import clsx from 'clsx';
import { AlertCircle, AlertTriangle, Clock, Cog, MapPin } from 'lucide-react';
import { Card, CardHeader, Pill } from '../ui/primitives';
import type { Defect } from '../../data/types';

const severityTile: Record<Defect['severity'], string> = {
  high: 'bg-bad-bg text-bad',
  medium: 'bg-warn-bg text-brand',
  low: 'bg-panel text-ink-2',
};

const severityText: Record<Defect['severity'], string> = {
  high: 'text-bad',
  medium: 'text-brand',
  low: 'text-ink-2',
};

const severityLabel: Record<Defect['severity'], string> = {
  high: 'High severity',
  medium: 'Medium severity',
  low: 'Low severity',
};

const chip =
  'inline-flex max-w-full items-center gap-1.5 rounded-lg border border-line bg-panel/60 px-2 py-1 text-xs text-ink';

export function DefectCard({ defect }: { defect: Defect }) {
  const station = 'Station ' + defect.stationId.replace('s', '');

  return (
    <Card className="flex h-full min-h-[248px] flex-col p-5">
      <CardHeader
        icon={
          <span className="text-bad">
            <AlertCircle size={18} strokeWidth={1.75} />
          </span>
        }
        title={
          <>
            Defect <span className="font-normal text-ink-2">(Inspection)</span>
          </>
        }
        right={
          <Pill tone="bad" dot className="shrink-0">
            Defect
          </Pill>
        }
      />

      <div className="mt-4 flex flex-1 flex-col">
        <div className="flex items-start gap-3">
          <span
            className={clsx('grid h-10 w-10 shrink-0 place-items-center rounded-xl', severityTile[defect.severity])}
            aria-hidden="true"
          >
            <AlertTriangle size={18} strokeWidth={1.75} />
          </span>
          <div className="min-w-0">
            <h3 className="font-display text-base font-semibold leading-snug text-ink">{defect.title}</h3>
            <p className={clsx('mt-0.5 text-xs font-medium', severityText[defect.severity])}>
              {severityLabel[defect.severity]}
            </p>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <span className={chip}>
            <Cog size={13} strokeWidth={1.75} className="shrink-0 text-ink-2" />
            <span className="min-w-0 truncate">{defect.asset}</span>
          </span>
          <span className={chip}>
            <MapPin size={13} strokeWidth={1.75} className="shrink-0 text-ink-2" />
            {station}
          </span>
        </div>

        <p className="mt-3 text-sm leading-relaxed text-ink-2">{defect.detail}</p>

        <div className="mt-auto flex items-center justify-between gap-3 border-t border-line pt-4 text-xs text-ink-2">
          <span className="inline-flex items-center gap-1.5">
            <Clock size={13} strokeWidth={1.75} className="text-ink-3" />
            Detected <span className="font-medium tabular-nums text-ink">{defect.detectedAt}</span>
          </span>
          <span className="tabular-nums">
            Ref <span className="font-medium text-ink">{defect.id.toUpperCase()}</span>
          </span>
        </div>
      </div>
    </Card>
  );
}
