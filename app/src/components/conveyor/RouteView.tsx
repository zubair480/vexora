// Route View: the conveyor "digital twin". SVG illustration with HTML chips, tooltip and the
// defect callout laid over it in the same aspect box, so they stay aligned at any card width.
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { Conveyor, Defect } from '../../data/types';
import { DefectCallout } from './DefectCallout';
import { RouteIllustration } from './RouteIllustration';
import { StationChip, type ChipDensity } from './StationChip';
import { CHIP_Y, VB, X0, X1, pctX, pctY, stationX } from './route-geometry';

/** Live border-box size of an element (0 until first layout). */
function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => setSize({ width: el.offsetWidth, height: el.offsetHeight });
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size] as const;
}

/** Distance from the head pulley, rounded to 5 m. StationList uses the same rule. */
const metresFromHead = (position: number, lengthM: number) => Math.round((position * lengthM) / 5) * 5;

export function RouteView({
  conveyor,
  defect,
  onSelectDefect,
}: {
  conveyor: Conveyor;
  defect: Defect;
  /** Called when the defect callout is clicked or activated from the keyboard. */
  onSelectDefect?: () => void;
}) {
  const [wrapRef, { width }] = useElementSize<HTMLDivElement>();
  const [calloutRef, { height: calloutH }] = useElementSize<HTMLButtonElement>();
  const idPrefix = `rg${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const [activeId, setActiveId] = useState<string | null>(null);

  const stations = [...conveyor.stations].sort((a, b) => a.position - b.position);
  const defectStation =
    stations.find((s) => s.id === defect.stationId) ?? stations.find((s) => s.status === 'defect') ?? null;

  const density: ChipDensity = width === 0 || width >= 860 ? 'full' : width >= 520 ? 'compact' : 'icon';
  const compact = density !== 'full';

  // The callout is sized in px while the box scales: convert its measured height to viewBox units
  // so the leader line ends just behind its top edge.
  const scale = width ? width / VB.w : 1;
  const calloutTopY = VB.h * 0.975 - (calloutH || (compact ? 66 : 78)) / scale;

  const activate = useCallback((id: string) => setActiveId(id), []);
  const deactivate = useCallback(() => setActiveId(null), []);

  useEffect(() => {
    if (!activeId) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setActiveId(null);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeId]);

  const lengthLabel = `${conveyor.lengthM.toLocaleString('en-US')} m`;

  return (
    <div className="relative my-auto w-full">
      <div
        ref={wrapRef}
        className="relative w-full overflow-visible rounded-2xl"
        style={{ aspectRatio: `${VB.w} / ${VB.h}` }}
      >
        <RouteIllustration
          stations={stations}
          defectStation={defectStation}
          activeId={activeId}
          onHover={setActiveId}
          calloutTopY={calloutTopY}
          idPrefix={idPrefix}
        />

        {stations.map((s, i) => (
          <StationChip
            key={s.id}
            station={s}
            order={i}
            xPct={pctX(stationX(s.position))}
            yPct={pctY(CHIP_Y)}
            active={activeId === s.id}
            density={density}
            distanceM={metresFromHead(s.position, conveyor.lengthM)}
            tipId={`${idPrefix}-tip-${s.id}`}
            onActivate={() => activate(s.id)}
            onDeactivate={deactivate}
          />
        ))}

        {defectStation && (
          <DefectCallout
            ref={calloutRef}
            station={defectStation}
            defect={defect}
            compact={compact}
            active={activeId === defectStation.id}
            onActivate={() => activate(defectStation.id)}
            onDeactivate={deactivate}
            onSelect={() => onSelectDefect?.()}
          />
        )}

        {/* route length ruler, either side of the callout */}
        <div
          className="pointer-events-none absolute bottom-[4%] flex items-center gap-2 text-[11px] tabular-nums text-ink-3"
          style={{ left: `${pctX(X0)}%` }}
          aria-hidden
        >
          <span className="h-px w-5 bg-ink-3/50" />
          {compact ? 'Head' : 'Head · 0 m'}
        </div>
        <div
          className="pointer-events-none absolute bottom-[4%] flex items-center gap-2 text-[11px] tabular-nums text-ink-3"
          style={{ right: `${100 - pctX(X1)}%` }}
          aria-hidden
        >
          {compact ? lengthLabel : `Tail · ${lengthLabel}`}
          <span className="h-px w-5 bg-ink-3/50" />
        </div>
      </div>
      <p className="sr-only">
        {conveyor.id}, {lengthLabel}, {conveyor.idlers} idlers. Stations:{' '}
        {stations.map((s) => `${s.name} ${s.role} ${s.status}${s.note ? ` (${s.note})` : ''}`).join('; ')}.
      </p>
    </div>
  );
}
