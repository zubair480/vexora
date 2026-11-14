import { useEffect, useId, useMemo, useState } from 'react';
import { useReducedMotion } from 'framer-motion';
import clsx from 'clsx';
import { Activity, BatteryMedium, BluetoothOff, RotateCw, Watch } from 'lucide-react';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import { Card, CardHeader, Pill } from '../ui/primitives';
import type { BandState, SensorSample } from '../../data/types';

type Mode = 'live' | 'stale' | 'disconnected';

const WINDOW = 60;
const TICK_MS = 500;
const MODES: Mode[] = ['live', 'stale', 'disconnected'];

/** 60 consecutive samples starting at `offset`, wrapping, with `t` re-indexed to 0..59. */
function sampleWindow(samples: SensorSample[], offset: number): SensorSample[] {
  const len = samples.length;
  if (len === 0) return [];
  const start = offset % len;
  const out: SensorSample[] = [];
  for (let i = 0; i < WINDOW; i++) {
    const s = samples[(start + i) % len];
    out.push({ ...s, t: i });
  }
  return out;
}

const CHANNELS = {
  accel: {
    label: 'Accelerometer (m/s²)',
    Icon: Activity,
    iconClass: 'text-info',
    badgeClass: 'bg-info-bg text-info',
    stroke: 'rgb(var(--info))',
    ticks: [-2, 0, 2],
    domain: [-2.5, 2.5] as [number, number],
    digits: 2,
  },
  gyro: {
    label: 'Gyroscope (°/s)',
    Icon: RotateCw,
    iconClass: 'text-sensor',
    badgeClass: 'bg-sensor-bg text-sensor',
    stroke: 'rgb(var(--sensor))',
    ticks: [-200, 0, 200],
    domain: [-250, 250] as [number, number],
    digits: 1,
  },
} as const;

function SensorChart({ channel, data, rms }: { channel: keyof typeof CHANNELS; data: SensorSample[]; rms: number }) {
  const c = CHANNELS[channel];
  const uid = useId().replace(/:/g, '');
  const gradId = `ls-${uid}-${channel}`;
  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <c.Icon size={16} strokeWidth={1.75} className={clsx('shrink-0', c.iconClass)} />
          <span className="truncate text-sm font-medium text-ink">{c.label}</span>
        </div>
        <span className={clsx('shrink-0 rounded-md px-2 py-0.5 text-xs tabular-nums', c.badgeClass)}>
          RMS <span className="font-display font-semibold">{rms.toFixed(c.digits)}</span>
        </span>
      </div>
      <div className="mt-1.5 h-[72px] w-full" role="img" aria-label={`${c.label}, RMS ${rms.toFixed(c.digits)}`}>
        <ResponsiveContainer width="100%" height={72}>
          <AreaChart data={data} margin={{ top: 4, right: 2, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={c.stroke} stopOpacity={0.18} />
                <stop offset="100%" stopColor={c.stroke} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="rgb(var(--line))" />
            <XAxis dataKey="t" hide />
            <YAxis
              width={28}
              ticks={c.ticks}
              domain={c.domain}
              allowDataOverflow
              axisLine={false}
              tickLine={false}
              interval={0}
              tick={{ fontSize: 10, fill: 'rgb(var(--ink-3))' }}
            />
            <Area
              type="monotone"
              dataKey={channel}
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

export function LiveSensorCard({ band }: { band: BandState }) {
  const derived: Mode = !band.connected ? 'disconnected' : !band.live ? 'stale' : 'live';
  const [override, setOverride] = useState<Mode | null>(null);
  const mode = override ?? derived;

  const [offset, setOffset] = useState(0);
  const reduce = useReducedMotion();
  useEffect(() => {
    if (mode !== 'live' || reduce) return;
    const id = window.setInterval(() => setOffset((o) => o + 1), TICK_MS);
    return () => window.clearInterval(id);
  }, [mode, reduce]);

  const data = useMemo(() => sampleWindow(band.samples, offset), [band.samples, offset]);

  const pill =
    mode === 'live' ? (
      <Pill tone="ok" className="[&>span]:motion-safe:animate-pulse" dot>
        Connected
      </Pill>
    ) : mode === 'stale' ? (
      <Pill tone="warn">Stale · 2 min</Pill>
    ) : (
      <Pill tone="neutral">Disconnected</Pill>
    );

  const cycle = () => setOverride(MODES[(MODES.indexOf(mode) + 1) % MODES.length]);

  return (
    <Card className="flex h-full min-h-[296px] flex-col p-5">
      <CardHeader
        icon={<Watch size={18} strokeWidth={1.75} />}
        title="Vexo Band — Live"
        right={
          <div className="flex flex-wrap items-center justify-end gap-x-2.5 gap-y-1">
            {pill}
            <span className="inline-flex items-center gap-1 text-ink-2" title="Band battery">
              <BatteryMedium size={16} strokeWidth={1.75} />
              <span className="text-xs font-medium tabular-nums">{band.battery}%</span>
            </span>
          </div>
        }
      />

      <div className="mt-4 flex flex-1 flex-col gap-3">
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
            {mode === 'stale' && (
              <span className="absolute right-1 top-8 z-10 rounded-md bg-brand-bg shadow-card px-2 py-0.5 text-[11px] font-medium text-brand">
                Last update 2 min ago
              </span>
            )}
            <div className={clsx('space-y-4 transition-opacity duration-300', mode === 'stale' && 'opacity-50')}>
              <SensorChart channel="accel" data={data} rms={band.accelRms} />
              <SensorChart channel="gyro" data={data} rms={band.gyroRms} />
            </div>
          </div>
        )}

        {import.meta.env.DEV && (
          <button
            type="button"
            onClick={cycle}
            className="mt-auto self-end rounded-md border border-dashed border-line px-1.5 py-0.5 text-[10px] text-ink-3 transition-colors hover:bg-panel focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/40"
          >
            dev: {mode}
          </button>
        )}
      </div>
    </Card>
  );
}
