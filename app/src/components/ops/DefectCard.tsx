import clsx from 'clsx';
import { AlertCircle, AlertTriangle, Clock, Cog, MapPin } from 'lucide-react';
import { Card, CardHeader, Pill, type Tone } from '../ui/primitives';
import type { Defect } from '../../data/types';
import { DASH, OpsEmptyCard } from './OpsEmpty';

// Known severities get a colour; anything else (or null) is shown neutrally, never guessed.
const SEVERITY: Record<string, { tile: string; text: string; label: string }> = {
  high: { tile: 'bg-bad-bg text-bad', text: 'text-bad', label: 'High severity' },
  medium: { tile: 'bg-warn-bg text-brand', text: 'text-brand', label: 'Medium severity' },
  low: { tile: 'bg-panel text-ink-2', text: 'text-ink-2', label: 'Low severity' },
};

const VERIFICATION: Record<string, { tone: Tone; label: string }> = {
  unverified: { tone: 'warn', label: 'Unverified worker report' },
  verified: { tone: 'ok', label: 'Verified' },
  rejected: { tone: 'neutral', label: 'Rejected' },
};

const chip =
  'inline-flex max-w-full items-center gap-1.5 rounded-lg border border-line bg-panel/60 px-2 py-1 text-xs text-ink';

const title = (
  <>
    Defect <span className="font-normal text-ink-2">(Inspection)</span>
  </>
);
const headerIcon = (
  <span className="text-bad">
    <AlertCircle size={18} strokeWidth={1.75} />
  </span>
);

function stationLabel(id: string) {
  const n = id.match(/\d+/)?.[0];
  return n ? `Station ${n}` : id;
}

export function DefectCard({ defect }: { defect: Defect | null }) {
  if (!defect) {
    return <OpsEmptyCard icon={<AlertCircle size={18} strokeWidth={1.75} />} title={title} message="No defects reported this shift" />;
  }

  const sev = defect.severity ? SEVERITY[defect.severity] : undefined;
  const sevLabel = sev?.label ?? (defect.severity ? `Severity: ${defect.severity}` : 'Severity not set');
  const verification = defect.verificationStatus
    ? VERIFICATION[defect.verificationStatus] ?? { tone: 'neutral' as Tone, label: defect.verificationStatus }
    : null;

  return (
    <Card className="flex h-full min-h-[248px] flex-col p-5">
      <CardHeader
        icon={headerIcon}
        title={title}
        right={
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            {verification && (
              <Pill tone={verification.tone} className="shrink-0">
                {verification.label}
              </Pill>
            )}
            <Pill tone="bad" dot className="shrink-0">
              Defect
            </Pill>
          </div>
        }
      />

      <div className="mt-4 flex flex-1 flex-col">
        <div className="flex items-start gap-3">
          <span
            className={clsx('grid h-10 w-10 shrink-0 place-items-center rounded-xl', sev?.tile ?? 'bg-panel text-ink-2')}
            aria-hidden="true"
          >
            <AlertTriangle size={18} strokeWidth={1.75} />
          </span>
          <div className="min-w-0">
            <h3 className="font-display text-base font-semibold leading-snug text-ink">{defect.title}</h3>
            <p className={clsx('mt-0.5 text-xs font-medium', sev?.text ?? 'text-ink-3')}>{sevLabel}</p>
          </div>
        </div>

        {(defect.asset || defect.stationId) && (
          <div className="mt-3 flex flex-wrap gap-2">
            {defect.asset && (
              <span className={chip}>
                <Cog size={13} strokeWidth={1.75} className="shrink-0 text-ink-2" />
                <span className="min-w-0 truncate">{defect.asset}</span>
              </span>
            )}
            {defect.stationId && (
              <span className={chip}>
                <MapPin size={13} strokeWidth={1.75} className="shrink-0 text-ink-2" />
                {stationLabel(defect.stationId)}
              </span>
            )}
          </div>
        )}

        <p className="mt-3 text-sm leading-relaxed text-ink-2">{defect.detail}</p>

        <div className="mt-auto flex items-center justify-between gap-3 border-t border-line pt-4 text-xs text-ink-2">
          <span className="inline-flex items-center gap-1.5">
            <Clock size={13} strokeWidth={1.75} className="text-ink-3" />
            Detected <span className="font-medium tabular-nums text-ink">{defect.detectedAt ?? DASH}</span>
          </span>
          <span className="tabular-nums">
            Ref <span className="font-medium text-ink">{defect.id.toUpperCase()}</span>
          </span>
        </div>
      </div>
    </Card>
  );
}
