// List view of the conveyor stations: one row per station, defect row highlighted.
import clsx from 'clsx';
import { useEffect, useRef } from 'react';
import type { Conveyor, Defect } from '../../data/types';
import { Pill, statusLabel, statusTone } from '../ui/primitives';

/** Distance from the head pulley, rounded to 5 m. RouteView uses the same rule. */
const metresFromHead = (position: number, lengthM: number) => Math.round((position * lengthM) / 5) * 5;

export function StationList({
  conveyor,
  defect,
  focusDefect = false,
}: {
  conveyor: Conveyor;
  defect: Defect;
  /** Move focus to the defect row on mount (used when arriving from the route view's defect callout). */
  focusDefect?: boolean;
}) {
  const stations = [...conveyor.stations].sort((a, b) => a.index - b.index);
  const defectRowRef = useRef<HTMLLIElement>(null);

  useEffect(() => {
    if (focusDefect) defectRowRef.current?.focus({ preventScroll: true });
  }, [focusDefect]);

  return (
    <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line/70" aria-label={`Stations on ${conveyor.id}`}>
      {stations.map((s) => {
        const isDefect = s.status === 'defect' || s.id === defect.stationId;
        const atM = metresFromHead(s.position, conveyor.lengthM);
        return (
          <li
            key={s.id}
            ref={isDefect ? defectRowRef : undefined}
            tabIndex={isDefect ? -1 : undefined}
            className={clsx(
              'grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-3.5 py-2 outline-none md:grid-cols-[28px_minmax(0,1fr)_minmax(0,1.1fr)_auto]',
              'focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-info/40',
              isDefect && 'bg-bad-bg/50 shadow-[inset_3px_0_0_rgb(var(--bad))]',
            )}
          >
            <span
              className={clsx(
                'flex h-6 w-6 items-center justify-center rounded-full font-display text-xs font-semibold tabular-nums',
                isDefect ? 'bg-bad-bg text-bad ring-1 ring-bad/25' : 'bg-panel text-ink-2',
              )}
            >
              {s.index}
            </span>

            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className={clsx('text-[13px] font-semibold', isDefect ? 'text-bad' : 'text-ink')}>{s.name}</span>
                <span className="truncate text-xs text-ink-2">{s.role}</span>
              </div>
              <div className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-ink-3">
                <span className="truncate">{s.asset ?? '—'}</span>
                <span className="shrink-0 tabular-nums">· ≈ {atM.toLocaleString('en-US')} m</span>
              </div>
              <div
                className="mt-1.5 hidden h-1 w-full max-w-[220px] overflow-hidden rounded-full bg-panel 2xl:block"
                role="img"
                aria-label={`Position ${Math.round(s.position * 100)}% along the belt`}
              >
                <div
                  className={clsx('h-full rounded-full', isDefect ? 'bg-bad/70' : 'bg-ink-3/50')}
                  style={{ width: `${Math.max(2, s.position * 100)}%` }}
                />
              </div>
            </div>

            <p className="hidden min-w-0 text-xs text-ink-2 md:block">{s.note}</p>

            <div className="flex flex-wrap items-center justify-end gap-1.5">
              {s.vibrationDelta != null && (
                <Pill tone="bad" className="tabular-nums">
                  +{s.vibrationDelta}% vs baseline
                </Pill>
              )}
              <Pill tone={statusTone[s.status]} dot>
                {statusLabel[s.status]}
              </Pill>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
