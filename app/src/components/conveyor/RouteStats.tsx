// Compact digital-twin readout under the route scene. RouteView shows it only when the card is
// taller than the scene needs (the card stretches to its grid row at xl), so that height carries
// content instead of blank space.
import { forwardRef, type ReactNode } from 'react';
import clsx from 'clsx';
import type { Conveyor } from '../../data/types';

function Tile({ label, value, sub, children }: { label: string; value: string; sub?: string; children?: ReactNode }) {
  return (
    <div className="h-16 min-w-0 overflow-hidden rounded-xl border border-line/70 bg-panel/60 px-3 py-2">
      <div className="truncate text-[11px] text-ink-3">{label}</div>
      <div className="mt-0.5 flex min-w-0 items-baseline gap-1.5">
        <span className="whitespace-nowrap font-display text-[15px] font-semibold tabular-nums text-ink">{value}</span>
        {sub && <span className="truncate text-[11px] text-ink-2">{sub}</span>}
      </div>
      {children}
    </div>
  );
}

export const RouteStats = forwardRef<HTMLDivElement, { conveyor: Conveyor }>(function RouteStats({ conveyor }, ref) {
  const counts = { ok: 0, attention: 0, defect: 0 };
  for (const s of conveyor.stations) counts[s.status] += 1;
  const flagged = counts.attention + counts.defect;
  const health = Math.max(0, Math.min(100, conveyor.beltHealth));
  const healthTone = health >= 85 ? 'bg-ok' : health >= 60 ? 'bg-warn' : 'bg-bad';

  return (
    <div ref={ref} className="grid grid-cols-4 gap-2.5" aria-label={`${conveyor.id} route summary`}>
      <Tile label="Belt health" value={`${health}%`}>
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-line" aria-hidden>
          <div className={clsx('h-full rounded-full', healthTone)} style={{ width: `${health}%` }} />
        </div>
      </Tile>
      <Tile label="Route length" value={`${conveyor.lengthM.toLocaleString('en-US')} m`} sub="head → tail" />
      <Tile
        label="Idlers"
        value={conveyor.idlers.toLocaleString('en-US')}
        sub={flagged ? `${flagged} flagged` : 'all nominal'}
      />
      <Tile
        label="Stations"
        value={`${counts.ok}/${conveyor.stations.length} OK`}
        sub={counts.defect ? `${counts.defect} defect` : counts.attention ? `${counts.attention} attention` : undefined}
      />
    </div>
  );
});
