// Segmented Route / List toggle with tab semantics, arrow-key support and a sliding active pill.
import clsx from 'clsx';
import { motion, useReducedMotion } from 'framer-motion';
import { List, MapPin } from 'lucide-react';
import { useRef, type KeyboardEvent } from 'react';

export type RouteMapView = 'route' | 'list';

const options: { value: RouteMapView; label: string; Icon: typeof MapPin }[] = [
  { value: 'route', label: 'Route View', Icon: MapPin },
  { value: 'list', label: 'List View', Icon: List },
];

export function ViewToggle({
  value,
  onChange,
  idPrefix,
}: {
  value: RouteMapView;
  onChange: (v: RouteMapView) => void;
  idPrefix: string;
}) {
  const reduce = useReducedMotion();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = options.findIndex((o) => o.value === value);
    let next = -1;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (i + 1) % options.length;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (i - 1 + options.length) % options.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = options.length - 1;
    if (next < 0) return;
    e.preventDefault();
    onChange(options[next].value);
    refs.current[next]?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label="Route map view"
      onKeyDown={onKeyDown}
      className="inline-flex shrink-0 rounded-xl border border-line/70 bg-panel p-1"
    >
      {options.map(({ value: v, label, Icon }, i) => {
        const active = v === value;
        return (
          <button
            key={v}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="tab"
            id={`${idPrefix}-tab-${v}`}
            aria-selected={active}
            aria-controls={`${idPrefix}-panel`}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(v)}
            className={clsx(
              'relative inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/40',
              active ? 'text-ink' : 'text-ink-2 hover:text-ink',
            )}
          >
            {active && (
              <motion.span
                layoutId={`${idPrefix}-seg`}
                aria-hidden
                className="absolute inset-0 rounded-lg bg-surface shadow-card"
                transition={reduce ? { duration: 0 } : { type: 'spring', stiffness: 400, damping: 32 }}
              />
            )}
            <Icon size={14} aria-hidden className="relative" />
            <span className="relative whitespace-nowrap">{label}</span>
          </button>
        );
      })}
    </div>
  );
}
