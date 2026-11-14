// Legend for the conveyor route map: status dots with live counts derived from the stations.
import clsx from 'clsx';
import type { Station, Status } from '../../data/types';

const items: { status: Status; label: string; dot: string }[] = [
  { status: 'ok', label: 'OK', dot: 'bg-ok' },
  { status: 'attention', label: 'Attention', dot: 'bg-warn' },
  { status: 'defect', label: 'Defect', dot: 'bg-bad' },
];

export function RouteLegend({ stations, className }: { stations: Station[]; className?: string }) {
  const counts: Record<Status, number> = { ok: 0, attention: 0, defect: 0 };
  for (const s of stations) counts[s.status] += 1;

  return (
    <ul className={clsx('flex flex-wrap items-center gap-x-4 gap-y-1.5', className)} aria-label="Station status legend">
      {items.map(({ status, label, dot }) => {
        const n = counts[status];
        return (
          <li key={status} className="inline-flex items-center gap-1.5 text-xs text-ink-2">
            <span aria-hidden className={clsx('h-2 w-2 rounded-full', dot, n === 0 && 'opacity-40')} />
            <span>{label}</span>
            <span className={clsx('font-medium tabular-nums', n === 0 ? 'text-ink-3' : 'text-ink')}>({n})</span>
          </li>
        );
      })}
    </ul>
  );
}
