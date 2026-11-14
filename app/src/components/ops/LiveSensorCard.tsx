// Live Vexo band card. `band` mirrors the Vexo SDK (BandState in data/types.ts); `controls` comes
// from useVexoBand and is the only way this card talks to the band (never the SDK directly).
import { useEffect, useId, useMemo, useState } from 'react';
import { useReducedMotion } from 'framer-motion';
import clsx from 'clsx';
import {
  Activity,
  BatteryCharging,
  BatteryMedium,
  BluetoothOff,
  ChevronRight,
  Hand,
  RotateCw,
  Signal,
  Watch,
} from 'lucide-react';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import type { VexoBand } from '../../band/useVexoBand';
import { accelDeviation, gyroSigned } from '../../band/motion';
import type { BandState, MotionSample } from '../../data/types';
import { Card, CardHeader, Pill, type Tone } from '../ui/primitives';

type Mode = 'live' | 'reconnecting' | 'stale' | 'disconnected';
type Point = { i: number; accel: number; gyro: number };

const WINDOW = 100;     // samples on screen (2 s at 50 Hz)
const TICK_MS = 100;    // demo data: how often the window scrolls
const STEP = 5;         // demo data: samples per tick (50 Hz feel)
const MODES: Mode[] = ['live', 'reconnecting', 'stale', 'disconnected'];

// Known sources; anything unexpected falls back to a neutral badge showing the raw value.
const SOURCES: Record<string, { label: string; tone: Tone }> = {
  mock: { label: 'Demo data', tone: 'neutral' },
  sim: { label: 'Pretend band', tone: 'plum' },
  ble: { label: 'Bluetooth', tone: 'info' },
  serial: { label: 'USB', tone: 'info' },
  backend: { label: 'BeltGuard server', tone: 'brand' },
};

function toPoints(samples: MotionSample[]): Point[] {
  return samples.map((s, i) => ({ i, accel: +accelDeviation(s).toFixed(3), gyro: +gyroSigned(s).toFixed(1) }));
}

/** WINDOW consecutive samples starting at `offset`, wrapping (demo data only). */
function loopWindow(samples: MotionSample[], offset: number): MotionSample[] {
  const len = samples.length;
  if (len <= WINDOW) return samples;
  const out: MotionSample[] = [];
  for (let k = 0; k < WINDOW; k++) out.push(samples[(offset + k) % len]);
  return out;
}

/** Symmetric axis bound: at least `min`, rounded up to a tidy step. */
function bound(values: number[], min: number, step: number): number {
  const peak = values.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
  return Math.max(min, Math.ceil((peak * 1.15) / step) * step);
}

const CHANNELS = {
  accel: {
    label: 'Accelerometer (m/s²)',
    Icon: Activity,
    iconClass: 'text-info',
    badgeClass: 'bg-info-bg text-info',
    stroke: 'rgb(var(--info))',
    min: 1,
    step: 0.5,
    digits: 2,
  },
  gyro: {
    label: 'Gyroscope (°/s)',
    Icon: RotateCw,
    iconClass: 'text-sensor',
    badgeClass: 'bg-sensor-bg text-sensor',
    stroke: 'rgb(var(--sensor))',
    min: 20,
    step: 10,
    digits: 1,
  },
} as const;

function SensorChart({ channel, data, rms }: { channel: keyof typeof CHANNELS; data: Point[]; rms: number | null }) {
  const c = CHANNELS[channel];
  const uid = useId().replace(/:/g, '');
  const gradId = `ls-${uid}-${channel}`;
  const b = bound(data.map((p) => p[channel]), c.min, c.step);
  const rmsText = rms === null ? '—' : rms.toFixed(c.digits);
  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <c.Icon size={16} strokeWidth={1.75} className={clsx('shrink-0', c.iconClass)} />
          <span className="truncate text-sm font-medium text-ink">{c.label}</span>
        </div>
        <span className={clsx('shrink-0 rounded-md px-2 py-0.5 text-xs tabular-nums', c.badgeClass)}>
          RMS <span className="font-display font-semibold">{rmsText}</span>
        </span>
      </div>
      <div className="mt-1.5 h-[72px] w-full" role="img" aria-label={`${c.label}, RMS ${rms === null ? 'not available' : rmsText}`}>
        <ResponsiveContainer width="100%" height={72}>
          <AreaChart data={data} margin={{ top: 6, right: 2, bottom: 6, left: 0 }}>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={c.stroke} stopOpacity={0.18} />
                <stop offset="100%" stopColor={c.stroke} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="rgb(var(--line))" />
            <XAxis dataKey="i" hide />
            <YAxis
              width={30}
              ticks={[-b, 0, b]}
              domain={[-b, b]}
              allowDataOverflow
              axisLine={false}
              tickLine={false}
              interval={0}
              tick={{ fontSize: 10, fill: 'rgb(var(--ink-3))' }}
            />
            <Area
              type="monotone"
              dataKey={channel}
              baseValue={0}
              stroke={c.stroke}
              strokeWidth={1.5}
              fill={`url(#${gradId})`}
              dot={false}
              activeDot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function clock(ms: number) {
  return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function BrowserConnect({ band, controls }: { band: BandState; controls: Omit<VexoBand, 'state'> }) {
  const [busy, setBusy] = useState(false);
  const run = (fn: () => Promise<void>) => {
    setBusy(true);
    fn().finally(() => setBusy(false));
  };
  const btn =
    'rounded-lg border border-line bg-surface px-2.5 py-1 text-xs font-medium text-ink-2 transition-colors hover:bg-panel disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/40';
  const onSdk = band.source !== 'mock';
  return (
    <details className="group text-xs">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded text-ink-3 hover:text-ink-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/40 [&::-webkit-details-marker]:hidden">
        <ChevronRight size={13} className="transition-transform group-open:rotate-90" />
        Connect from this browser
      </summary>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <button type="button" disabled={busy} className={btn} onClick={() => run(() => controls.connect('ble'))}>Bluetooth</button>
        <button type="button" disabled={busy} className={btn} onClick={() => run(() => controls.connect('serial'))}>USB</button>
        <button type="button" disabled={busy} className={btn} onClick={() => run(() => controls.connect('sim'))}>Pretend band</button>
        {onSdk && (
          <>
            <button type="button" disabled={busy} className={btn} onClick={() => run(controls.identify)}>Identify</button>
            <button type="button" disabled={busy} className={clsx(btn, 'text-bad')} onClick={() => run(controls.disconnect)}>Disconnect</button>
          </>
        )}
      </div>
    </details>
  );
}

export function LiveSensorCard({ band, controls }: { band: BandState; controls?: Omit<VexoBand, 'state'> }) {
  const derived: Mode = band.reconnecting ? 'reconnecting' : !band.connected ? 'disconnected' : !band.live ? 'stale' : 'live';
  const [override, setOverride] = useState<Mode | null>(null);
  const mode = override ?? derived;
  const isDemo = band.source === 'mock';

  // Demo data is a fixed buffer, so scroll through it to look live. Real sources update samples themselves.
  const [offset, setOffset] = useState(0);
  const reduce = useReducedMotion();
  useEffect(() => {
    if (!isDemo || mode !== 'live' || reduce) return;
    const id = window.setInterval(() => setOffset((o) => o + STEP), TICK_MS);
    return () => window.clearInterval(id);
  }, [isDemo, mode, reduce]);

  const data = useMemo(
    () => toPoints(isDemo ? loopWindow(band.samples, offset) : band.samples.slice(-WINDOW)),
    [band.samples, isDemo, offset],
  );

  const source = SOURCES[String(band.source)] ?? { label: String(band.source), tone: 'neutral' as Tone };
  const pct = band.battery?.percent;
  const BatteryIcon = band.battery?.charging ? BatteryCharging : BatteryMedium;
  const lastSampleAt = band.samples.length ? band.samples[band.samples.length - 1].time : null;
  const rate = band.effectiveRate ?? band.motionRate;
  const quality = [
    rate != null ? `${Math.round(rate)} Hz` : null,
    band.completeness != null ? `${Math.round(band.completeness * 100)}%` : null,
  ].filter(Boolean).join(' · ');

  // A replay is recorded data: label it plainly and never show the live "Connected" pulse.
  const pill = band.replay ? (
    <Pill tone="warn">Replay — recorded data</Pill>
  ) :
    mode === 'live' ? (
      <Pill tone="ok" className="[&>span]:motion-safe:animate-pulse" dot>Connected</Pill>
    ) : mode === 'reconnecting' ? (
      <Pill tone="warn" dot>Reconnecting</Pill>
    ) : mode === 'stale' ? (
      <Pill tone="warn">Stale</Pill>
    ) : (
      <Pill tone="neutral">Disconnected</Pill>
    );

  const cycle = () => setOverride(MODES[(MODES.indexOf(mode) + 1) % MODES.length]);

  return (
    <Card className="flex h-full min-h-[296px] flex-col p-5">
      <CardHeader
        icon={<Watch size={18} strokeWidth={1.75} />}
        title={band.replay ? 'Vexo Band — Replay' : 'Vexo Band — Live'}
        subtitle={band.name ?? undefined}
        right={
          <div className="flex flex-wrap items-center justify-end gap-x-2.5 gap-y-1">
            {pill}
            {pct !== undefined && (
              <span className="inline-flex items-center gap-1 text-ink-2" title={`Battery${band.battery?.charging ? ', charging' : ''}`}>
                <BatteryIcon size={16} strokeWidth={1.75} />
                <span className="text-xs font-medium tabular-nums">{pct}%</span>
              </span>
            )}
          </div>
        }
      />

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-3">
        <Pill tone={source.tone} className="!px-2 !py-0.5 text-[11px]">{source.label}</Pill>
        {quality && (
          <span className="tabular-nums" title={band.effectiveRate != null ? 'Samples per second arriving · share received' : 'Samples per second asked for'}>
            {quality}
          </span>
        )}
        {band.rssi !== null && (
          <span className="inline-flex items-center gap-1" title="Signal strength">
            <Signal size={13} strokeWidth={1.75} />
            <span className="tabular-nums">{band.rssi} dBm</span>
          </span>
        )}
        {band.lastGesture && (
          <span className="inline-flex items-center gap-1" title="Last gesture">
            <Hand size={13} strokeWidth={1.75} />
            {band.lastGesture.kind} · <span className="tabular-nums">{clock(band.lastGesture.time)}</span>
          </span>
        )}
      </div>

      <div className="mt-3 flex flex-1 flex-col gap-3">
        {mode === 'disconnected' ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-1.5 rounded-xl bg-panel/60 p-6 text-center">
            <span className="mb-1 grid h-10 w-10 place-items-center rounded-xl bg-surface shadow-card">
              <BluetoothOff size={20} strokeWidth={1.75} className="text-ink-3" />
            </span>
            <p className="text-sm font-medium text-ink">Band disconnected</p>
            <p className="label">Reconnect the Vexo band to resume live data.</p>
          </div>
        ) : (
          <div className="relative">
            {mode !== 'live' && (
              <span className="absolute right-1 top-8 z-10 rounded-md bg-warn-bg px-2 py-0.5 text-[11px] font-medium text-ink-2 shadow-card">
                {mode === 'reconnecting' ? 'Reconnecting…' : 'No new data'}
                {lastSampleAt !== null && <> · last {clock(lastSampleAt)}</>}
              </span>
            )}
            <div className={clsx('space-y-4 transition-opacity duration-300', mode !== 'live' && 'opacity-50')}>
              <SensorChart channel="accel" data={data} rms={band.accelRms} />
              <SensorChart channel="gyro" data={data} rms={band.gyroRms} />
            </div>
          </div>
        )}

        {band.error && <p className="text-xs text-bad" role="alert">{band.error}</p>}

        <div className="mt-auto flex items-end justify-between gap-3">
          {controls ? <BrowserConnect band={band} controls={controls} /> : <span />}
          {import.meta.env.DEV && (
            <button
              type="button"
              onClick={cycle}
              className="shrink-0 rounded-md border border-dashed border-line px-1.5 py-0.5 text-[10px] text-ink-3 transition-colors hover:bg-panel focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/40"
            >
              dev: {mode}
            </button>
          )}
        </div>
      </div>
    </Card>
  );
}
