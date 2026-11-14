import clsx from 'clsx';
import { Activity, BarChart3, Bell, ClipboardCheck, Gauge, LayoutGrid, Leaf, ShieldCheck, Wrench, Workflow } from 'lucide-react';

const nav = [
  { label: 'Overview', icon: LayoutGrid, active: true },
  { label: 'Inspections', icon: ClipboardCheck },
  { label: 'Vibration (Vexo)', icon: Activity },
  { label: 'Exposure (HAV)', icon: ShieldCheck },
  { label: 'Work Orders', icon: Wrench },
  { label: 'Conveyors', icon: Workflow },
  { label: 'Events', icon: Bell },
  { label: 'Reports', icon: BarChart3 },
];

export function Sidebar() {
  return (
    <aside className="sticky top-0 hidden h-screen w-[240px] shrink-0 flex-col border-r border-line/70 bg-surface/60 px-4 py-6 backdrop-blur lg:flex">
      <div className="flex items-center gap-2.5 px-2">
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-brand-bg text-brand">
          <Gauge size={20} strokeWidth={2.2} />
        </span>
        <div>
          <div className="font-display text-[19px] font-bold leading-none tracking-tight">BeltGuard</div>
          <div className="mt-1 text-[10.5px] text-ink-2">Conveyor Safety &amp; HAV Management</div>
        </div>
      </div>

      <nav className="mt-8 flex flex-col gap-1">
        {nav.map(({ label, icon: Icon, active }) => (
          <a
            key={label}
            href="#"
            className={clsx(
              'flex items-center gap-3 rounded-xl px-3 py-2.5 text-[14px] transition-colors',
              active ? 'bg-brand-bg/80 font-semibold text-ink' : 'text-ink-2 hover:bg-panel hover:text-ink',
            )}
          >
            <Icon size={18} className={active ? 'text-brand' : ''} />
            {label}
          </a>
        ))}
      </nav>

      <div className="mt-auto rounded-card border border-line/70 bg-panel/70 p-4">
        <Leaf size={18} className="text-ok" />
        <p className="mt-2 text-[13px] leading-snug text-ink-2">
          Safer maintenance.<br />Healthier crews.<br />Reliable conveying.
        </p>
      </div>
    </aside>
  );
}
