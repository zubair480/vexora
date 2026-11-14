// Conveyor route map card: header, legend, Route/List toggle, and the animated body.
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { Route } from 'lucide-react';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { Conveyor, Defect } from '../../data/types';
import { Card, CardHeader } from '../ui/primitives';
import { RouteLegend } from './RouteLegend';
import { RouteView } from './RouteView';
import { VB } from './route-geometry';
import { StationList } from './StationList';
import { ViewToggle, type RouteMapView } from './ViewToggle';

const NUMBER_WORDS = ['Zero', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];

function countWord(n: number) {
  return NUMBER_WORDS[n] ?? n.toLocaleString('en-US');
}

export function ConveyorRouteMap({ conveyor, defect }: { conveyor: Conveyor; defect: Defect }) {
  const [view, setViewState] = useState<RouteMapView>('route');
  const [focusDefect, setFocusDefect] = useState(false);
  const setView = useCallback((v: RouteMapView) => {
    setFocusDefect(false);
    setViewState(v);
  }, []);
  const showDefectInList = useCallback(() => {
    setFocusDefect(true);
    setViewState('list');
  }, []);

  // Hold the panel at least at the route scene's base height so switching to the list does not
  // make the card jump; at xl the card also stretches to its grid row and the route view fills it.
  const [panelW, setPanelW] = useState(0);
  const roRef = useRef<ResizeObserver | null>(null);
  const panelRef = useCallback((el: HTMLDivElement | null) => {
    roRef.current?.disconnect();
    roRef.current = null;
    if (!el) return;
    const read = () => setPanelW(el.offsetWidth);
    read();
    roRef.current = new ResizeObserver(read);
    roRef.current.observe(el);
  }, []);
  useEffect(() => () => roRef.current?.disconnect(), []);
  const reduce = useReducedMotion();
  const idPrefix = useId().replace(/[^a-zA-Z0-9_-]/g, '');

  const n = conveyor.stations.length;
  const subtitle = `${countWord(n)} station${n === 1 ? '' : 's'} · ${conveyor.lengthM.toLocaleString('en-US')} m · ${conveyor.idlers.toLocaleString('en-US')} idlers`;

  const motionProps = reduce
    ? { initial: false as const, animate: { opacity: 1 }, exit: { opacity: 0 }, transition: { duration: 0 } }
    : {
        initial: { opacity: 0, y: 6 },
        animate: { opacity: 1, y: 0 },
        exit: { opacity: 0, y: -6 },
        transition: { duration: 0.18, ease: 'easeOut' as const },
      };

  return (
    <Card hover={false} className="flex h-full flex-col gap-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <CardHeader icon={<Route size={18} />} title={`Conveyor Route Map — ${conveyor.id}`} subtitle={subtitle} />
        </div>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 max-lg:w-full max-lg:justify-between">
          <RouteLegend stations={conveyor.stations} />
          <ViewToggle value={view} onChange={setView} idPrefix={idPrefix} />
        </div>
      </div>

      <div
        id={`${idPrefix}-panel`}
        role="tabpanel"
        aria-labelledby={`${idPrefix}-tab-${view}`}
        ref={panelRef}
        className="relative flex min-h-[240px] flex-1 flex-col"
        style={panelW ? { minHeight: Math.round((panelW * VB.h) / VB.w) } : undefined}
      >
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={view}
            className={view === 'route' ? 'flex flex-1 flex-col' : 'flex-1'}
            {...motionProps}
          >
            {view === 'route' ? (
              <RouteView conveyor={conveyor} defect={defect} onSelectDefect={showDefectInList} />
            ) : (
              <StationList conveyor={conveyor} defect={defect} focusDefect={focusDefect} />
            )}
          </motion.div>
        </AnimatePresence>
      </div>
    </Card>
  );
}
