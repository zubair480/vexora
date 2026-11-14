import { Bell, Bluetooth, ChevronDown, MapPin, Search } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Pill } from '../components/ui/primitives';
import type { DataMode } from '../api/useShift';
import { BAND_NUMBER } from '../band/config';
import type { VexoBand } from '../band/useVexoBand';
import type { Conveyor } from '../data/types';

/** Where the numbers on screen come from. Replay and demo data are always labelled. */
function SourcePill({ mode, replay }: { mode: DataMode; replay: boolean }) {
  if (mode === 'connecting') return <Pill tone="neutral" dot>Connecting…</Pill>;
  if (mode === 'demo') return <Pill tone="neutral" dot>Demo data · server offline</Pill>;
  if (replay) return <Pill tone="warn" dot>Replay — recorded data</Pill>;
  return <Pill tone="ok" dot>Live</Pill>;
}

/** The kit dashboard's Connect button, for the team's band. Bluetooth needs a click (browser rule). */
function BandButton({ vexo }: { vexo: VexoBand }) {
  const { phase, state, forwarding } = vexo;
  const busy = phase === 'connecting' || phase === 'setting-up';
  if (phase === 'connected' || phase === 'setting-up') {
    return (
      <div className="flex items-center gap-2">
        <Pill tone={phase === 'connected' ? 'ok' : 'neutral'} dot>
          <Bluetooth size={13} /> {state.name ?? 'Band'}
          {phase === 'setting-up' ? ' · setting up…' : forwarding === 'on' ? ' · streaming to BeltGuard' : ''}
        </Pill>
        <button
          type="button"
          onClick={() => void vexo.disconnect()}
          className="rounded-xl border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink-2 hover:bg-panel"
        >
          Disconnect
        </button>
      </div>
    );
  }
  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => void vexo.connect('ble')}
      title={BAND_NUMBER ? `Double-tap the band first (it blinks blue), then pick Vexo-HACK-${String(BAND_NUMBER).padStart(2, '0')}` : undefined}
      className="flex items-center gap-2 rounded-xl bg-brand px-3.5 py-2 text-sm font-semibold text-white shadow-card transition hover:brightness-105 disabled:opacity-60"
    >
      <Bluetooth size={16} />
      {busy ? 'Connecting… pick your band' : 'Connect Bluetooth'}
    </button>
  );
}

export function TopBar({ conveyor, mode, replay, vexo }: { conveyor: Conveyor; mode: DataMode; replay: boolean; vexo: VexoBand }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex items-center justify-between gap-4">
      <div className="flex items-center gap-4">
        <button className="flex items-center gap-2 rounded-xl border border-line bg-surface px-3 py-2 text-sm font-medium shadow-card">
          <MapPin size={16} className="text-ink-2" />
          {conveyor.site ?? 'Site'}
          <ChevronDown size={16} className="text-ink-3" />
        </button>
        <nav className="hidden text-xs font-medium uppercase tracking-[0.12em] text-ink-3 md:block">
          Site <span className="mx-1.5">/</span>
          {conveyor.pit && <>{conveyor.pit} <span className="mx-1.5">/</span></>}
          <span className="text-ink-2">{conveyor.id}</span>
        </nav>
      </div>
      <div className="flex items-center gap-3">
        <BandButton vexo={vexo} />
        <SourcePill mode={mode} replay={replay} />
        <span className="hidden text-sm text-ink-2 sm:block">
          {now.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })} ·{' '}
          {now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
        </span>
        <button className="rounded-full p-2 text-ink-2 hover:bg-panel" aria-label="Search"><Search size={18} /></button>
        <button className="relative rounded-full p-2 text-ink-2 hover:bg-panel" aria-label="Notifications">
          <Bell size={18} />
          <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-brand" />
        </button>
        <span className="grid h-9 w-9 place-items-center rounded-full bg-brand-bg font-display text-sm font-bold text-brand">JM</span>
      </div>
    </div>
  );
}
