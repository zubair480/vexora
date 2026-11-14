// Route View: the conveyor "digital twin". SVG illustration with HTML chips, tooltip and the
// defect callout laid over it in the same aspect box, so they stay aligned at any card width.
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { Conveyor, Defect } from '../../data/types';
import { DefectCallout } from './DefectCallout';
import { RouteStats } from './RouteStats';
import { VibrationProfile } from './VibrationProfile';
import { RouteIllustration } from './RouteIllustration';
import { StationChip, type ChipDensity } from './StationChip';
import { CHIP_Y, VB, X0, X1, pctX, stationX } from './route-geometry';

/** Fixed heights (px) of the optional blocks under the scene, and the gap between blocks. */
const STATS_H = 64;
const PROFILE_H = 72;
const GAP = 12;

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
const metresFromHead = (position: number, lengthM: number | null) =>
  lengthM == null ? null : Math.round((position * lengthM) / 5) * 5;

export function RouteView({
  conveyor,
  defect,
  onSelectDefect,
}: {
  conveyor: Conveyor;
  /** null = no open defect: no callout or halo. */
  defect: Defect | null;
  /** Called when the defect callout is clicked or activated from the keyboard. */
  onSelectDefect?: () => void;
}) {
  // The outer box fills whatever height the card gives it (the card stretches to its grid row at
  // xl). The scene keeps its proportions; extra height grows the viewBox, which lengthens the
  // leader lines and shows more backdrop instead of leaving an empty band above the chips.
  const [wrapRef, { width, height }] = useElementSize<HTMLDivElement>();
  const [calloutRef, { height: calloutH }] = useElementSize<HTMLButtonElement>();
  const idPrefix = `rg${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const [activeId, setActiveId] = useState<string | null>(null);

  const stations = [...conveyor.stations].sort((a, b) => a.position - b.position);
  const defectStation =
    defect == null
      ? null
      : (stations.find((s) => s.id === defect.stationId) ?? stations.find((s) => s.status === 'defect') ?? null);

  const density: ChipDensity = width === 0 || width >= 860 ? 'full' : width >= 520 ? 'compact' : 'icon';
  const compact = density !== 'full';

  // The callout is sized in px while the box scales: convert its measured height to viewBox units
  // so the leader line ends just behind its top edge.
  const scale = width ? width / VB.w : 1;
  // Height budget. At xl the card stretches to its grid row, which can be much taller than the
  // scene's base aspect. Spare height goes, in order, to: the route summary strip, the vibration
  // profile, then a taller scene (longer leaders, more backdrop) up to H_MAX viewBox units.
  const H_MAX = VB.h * 2.1;
  const baseH = width * (VB.h / VB.w);
  const spare = width && height ? height - baseH : 0;
  const showStats = spare >= STATS_H + GAP;
  const hasVibration = conveyor.stations.some((s) => s.vibrationDelta != null);
  const showProfile = hasVibration && spare >= STATS_H + PROFILE_H + 2 * GAP + 40;
  const sceneAvail = height - (showStats ? STATS_H + GAP : 0) - (showProfile ? PROFILE_H + GAP : 0);
  const vbH = width && height ? Math.min(H_MAX, Math.max(VB.h, sceneAvail / scale)) : VB.h;
  const boxH = vbH * scale;
  const dy = Math.round((vbH - VB.h) * 0.25);
  const calloutTopY = vbH * 0.975 - (calloutH || (compact ? 66 : 78)) / scale;

  const activate = useCallback((id: string) => setActiveId(id), []);
  const deactivate = useCallback(() => setActiveId(null), []);

  useEffect(() => {
    if (!activeId) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setActiveId(null);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeId]);

  const lengthLabel = conveyor.lengthM != null ? `${conveyor.lengthM.toLocaleString('en-US')} m` : null;

  return (
    <div
      ref={wrapRef}
      className="relative flex w-full flex-1 flex-col justify-center gap-3"
      style={{ minHeight: width ? Math.round((width * VB.h) / VB.w) : undefined, aspectRatio: width ? undefined : `${VB.w} / ${VB.h}` }}
    >
      <div
        className="relative w-full overflow-visible rounded-2xl"
        style={{ height: width ? boxH : undefined, aspectRatio: width ? undefined : `${VB.w} / ${VB.h}` }}
      >
        <RouteIllustration
          stations={stations}
          defectStation={defectStation}
          activeId={activeId}
          onHover={setActiveId}
          calloutTopY={calloutTopY}
          idPrefix={idPrefix}
          h={vbH}
          dy={dy}
        />

        {stations.map((s, i) => (
          <StationChip
            key={s.id}
            station={s}
            order={i}
            xPct={pctX(stationX(s.position))}
            yPct={(CHIP_Y / vbH) * 100}
            active={activeId === s.id}
            density={density}
            distanceM={metresFromHead(s.position, conveyor.lengthM)}
            tipId={`${idPrefix}-tip-${s.id}`}
            onActivate={() => activate(s.id)}
            onDeactivate={deactivate}
          />
        ))}

        {defect && defectStation && (
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
          {compact || lengthLabel == null ? 'Head' : 'Head · 0 m'}
        </div>
        <div
          className="pointer-events-none absolute bottom-[4%] flex items-center gap-2 text-[11px] tabular-nums text-ink-3"
          style={{ right: `${100 - pctX(X1)}%` }}
          aria-hidden
        >
          {lengthLabel == null ? 'Tail' : compact ? lengthLabel : `Tail · ${lengthLabel}`}
          <span className="h-px w-5 bg-ink-3/50" />
        </div>
      </div>
      {showProfile && <VibrationProfile stations={stations} idPrefix={idPrefix} />}
      {showStats && <RouteStats conveyor={conveyor} />}
      <p className="sr-only">
        {[conveyor.id, lengthLabel, conveyor.idlers != null ? `${conveyor.idlers} idlers` : null].filter(Boolean).join(', ')}. Stations:{' '}
        {stations.map((s) => [s.name, s.role, s.status].filter(Boolean).join(' ') + (s.note ? ` (${s.note})` : '')).join('; ')}.
      </p>
    </div>
  );
}
