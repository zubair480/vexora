// Shift event timeline: filterable horizontal rail of the key events this shift (BRIEF section 9).
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import clsx from 'clsx';
import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from 'framer-motion';
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ClipboardList,
  FileText,
  Footprints,
  Gauge,
  Search,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import { Card, CardHeader } from '../ui/primitives';
import type { EventKind, ShiftEvent } from '../../data/types';

// Literal class strings per kind so Tailwind JIT sees every one of them.
interface KindStyle {
  label: string;
  icon: LucideIcon;
  chip: string;   // chip background
  tile: string;   // icon tile (bg + icon colour)
  dot: string;    // rail dot
  halo: string;   // pulsing halo for the latest event
  ring: string;   // latest chip ring
  text: string;   // "Latest" micro-label
}

const KIND: Record<EventKind, KindStyle> = {
  walking: {
    label: 'Walking', icon: Footprints,
    chip: 'bg-info-bg/70', tile: 'bg-info-bg text-info', dot: 'bg-info', halo: 'bg-info/40', ring: 'ring-info/40', text: 'text-info',
  },
  inspection: {
    label: 'Inspection', icon: Search,
    chip: 'bg-ok-bg/70', tile: 'bg-ok-bg text-ok', dot: 'bg-ok', halo: 'bg-ok/40', ring: 'ring-ok/40', text: 'text-ok',
  },
  tool: {
    label: 'Tool Use', icon: Wrench,
    chip: 'bg-warn-bg/70', tile: 'bg-warn-bg text-brand', dot: 'bg-warn', halo: 'bg-warn/40', ring: 'ring-warn/40', text: 'text-brand',
  },
  workorder: {
    label: 'Work Order', icon: FileText,
    chip: 'bg-plum-bg/70', tile: 'bg-plum-bg text-plum', dot: 'bg-plum', halo: 'bg-plum/40', ring: 'ring-plum/40', text: 'text-plum',
  },
  defect: {
    label: 'Defect', icon: AlertCircle,
    chip: 'bg-bad-bg/70', tile: 'bg-bad-bg text-bad', dot: 'bg-bad', halo: 'bg-bad/40', ring: 'ring-bad/40', text: 'text-bad',
  },
  vibration: {
    label: 'Vibration', icon: Activity,
    chip: 'bg-sensor-bg/70', tile: 'bg-sensor-bg text-sensor', dot: 'bg-sensor', halo: 'bg-sensor/40', ring: 'ring-sensor/40', text: 'text-sensor',
  },
  exposure: {
    label: 'Exposure', icon: Gauge,
    chip: 'bg-panel/80', tile: 'bg-panel text-ink-2', dot: 'bg-ink-3', halo: 'bg-ink-3/40', ring: 'ring-ink-3/40', text: 'text-ink-2',
  },
};

const LEGEND_ORDER: EventKind[] = ['walking', 'inspection', 'tool', 'workorder', 'defect', 'vibration', 'exposure'];

type FilterId = 'all' | 'defect' | 'vibration' | 'exposure' | 'workorder';

const FILTERS: { id: FilterId; label: string; icon?: LucideIcon }[] = [
  { id: 'all', label: 'All' },
  { id: 'defect', label: 'Defects', icon: AlertTriangle },
  { id: 'vibration', label: 'Vibration', icon: Activity },
  { id: 'exposure', label: 'Exposure', icon: Gauge },
  { id: 'workorder', label: 'Work Orders', icon: ClipboardList },
];

/** "08:02" → minutes since midnight, for ordering. Unknown start sorts first, so "Latest" is a timed event. */
function toMinutes(hhmm: string | null): number {
  if (!hhmm) return -1;
  const [h, m] = hhmm.split(':').map(Number);
  return (h || 0) * 60 + (m || 0);
}

/** Provenance worth flagging on a chip; band / manual / derived events need no tag. */
const SOURCE_TAG: Record<string, string> = { replay: 'replay', synthetic: 'synthetic', ai: 'AI' };

function FilterTabs({ value, onChange }: { value: FilterId; onChange: (f: FilterId) => void }) {
  const groupId = useId();
  return (
    <LayoutGroup id={groupId}>
      <div role="group" aria-label="Filter events" className="inline-flex flex-wrap gap-0.5 rounded-xl bg-panel p-1">
        {FILTERS.map((f) => {
          const active = f.id === value;
          const Icon = f.icon;
          return (
            <button
              key={f.id}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(f.id)}
              className={clsx(
                'relative inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/40',
                active ? 'text-ink' : 'text-ink-2 hover:text-ink',
              )}
            >
              {active && (
                <motion.span
                  layoutId="tl-tab"
                  className="absolute inset-0 rounded-lg bg-surface shadow-card"
                  transition={{ type: 'spring', stiffness: 500, damping: 38 }}
                />
              )}
              {Icon && <Icon size={13} strokeWidth={2} className="relative" />}
              <span className="relative">{f.label}</span>
            </button>
          );
        })}
      </div>
    </LayoutGroup>
  );
}

function Legend() {
  return (
    <ul className="mt-3 hidden flex-wrap items-center gap-x-4 gap-y-1 md:flex" aria-label="Event types">
      {LEGEND_ORDER.map((k) => (
        <li key={k} className="inline-flex items-center gap-1.5 text-xs text-ink-2">
          <span className={clsx('h-2 w-2 rounded-full', KIND[k].dot)} />
          {KIND[k].label}
        </li>
      ))}
    </ul>
  );
}

function TimelineItem({ event, latest, fill }: { event: ShiftEvent; latest: boolean; fill: boolean }) {
  const k = KIND[event.kind];
  const Icon = k.icon;
  return (
    <motion.li
      layout
      initial={{ opacity: 0, x: 8 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 8 }}
      transition={{ duration: 0.18, ease: 'easeOut' }}
      className={clsx('relative flex w-[168px] shrink-0 flex-col', fill && 'xl:grow')}
    >
      {/* Dot sits on the hairline (centre at 5px). */}
      <div className="flex h-2.5 items-center pl-3">
        {latest ? (
          <span className="relative flex h-3.5 w-3.5">
            <span className={clsx('absolute inset-0 rounded-full motion-safe:animate-ping', k.halo)} />
            <span className={clsx('relative h-3.5 w-3.5 rounded-full ring-4 ring-surface', k.dot)} />
          </span>
        ) : (
          <span className={clsx('h-2.5 w-2.5 rounded-full ring-4 ring-surface', k.dot)} />
        )}
      </div>

      <div className={clsx('mt-3 flex flex-1 gap-2.5 rounded-xl p-3', k.chip, latest && clsx('shadow-card ring-1', k.ring))}>
        <span className={clsx('grid h-7 w-7 shrink-0 place-items-center rounded-lg', k.tile)}>
          <Icon size={15} strokeWidth={1.9} />
        </span>
        <div className="min-w-0">
          {latest && <p className={clsx('text-[10px] font-semibold uppercase tracking-wide', k.text)}>Latest</p>}
          <p className="flex items-center gap-1.5 text-[11px] font-semibold tabular-nums text-ink">
            <span>{event.end ? `${event.start ?? '—'} – ${event.end}` : event.start ?? 'Time unknown'}</span>
            {event.source && SOURCE_TAG[event.source] && (
              <span className="rounded bg-surface/80 px-1 py-px text-[9px] font-semibold uppercase tracking-wide text-ink-3 ring-1 ring-line">
                {SOURCE_TAG[event.source]}
              </span>
            )}
          </p>
          <p className="truncate text-xs font-semibold text-ink">{event.title}</p>
          <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-ink-2">{event.detail}</p>
        </div>
      </div>
    </motion.li>
  );
}

export function ShiftTimeline({ events, conveyorId }: { events: ShiftEvent[]; conveyorId: string }) {
  const [filter, setFilter] = useState<FilterId>('all');

  const visible = useMemo(() => {
    const list = filter === 'all' ? events : events.filter((e) => e.kind === filter);
    // Stable sort by start time; data order breaks ties.
    return list
      .map((e, i) => ({ e, i }))
      .sort((a, b) => toMinutes(a.e.start) - toMinutes(b.e.start) || a.i - b.i)
      .map(({ e }) => e);
  }, [events, filter]);

  const latestId = visible.length ? visible[visible.length - 1].id : null;
  const fill = filter === 'all';

  // Keep the latest event (right end of the rail) in view on load and on filter change.
  const railRef = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  useEffect(() => {
    const el = railRef.current;
    if (el) el.scrollTo({ left: el.scrollWidth, behavior: reduce ? 'auto' : 'smooth' });
  }, [filter, visible.length, reduce]);

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <CardHeader
          icon={<Activity size={18} strokeWidth={1.75} />}
          title={<>Event Timeline — {conveyorId}</>}
          subtitle="Key events this shift"
        />
        <FilterTabs value={filter} onChange={setFilter} />
      </div>

      <div
        ref={railRef}
        tabIndex={0}
        role="region"
        aria-label="Shift events"
        className="mt-5 overflow-x-auto rounded-xl pb-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/40"
      >
        {visible.length === 0 ? (
          <div className="grid min-h-[104px] place-items-center rounded-xl bg-panel/60">
            <p className="label">No events of this type this shift</p>
          </div>
        ) : (
          <div className="relative w-max min-w-full">
            <span aria-hidden className="pointer-events-none absolute left-0 right-0 top-[5px] h-px bg-line" />
            <ol className="relative flex gap-3">
              <AnimatePresence initial={false} mode="popLayout">
                {visible.map((e) => (
                  <TimelineItem key={e.id} event={e} latest={e.id === latestId} fill={fill} />
                ))}
              </AnimatePresence>
            </ol>
          </div>
        )}
      </div>
      {fill && <Legend />}
    </Card>
  );
}
