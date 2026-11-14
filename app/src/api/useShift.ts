// Shift data from the BeltGuard server, with the demo data as a fallback.
//
// The Python server (backend/, port 8010) owns the band link and builds the dashboard's ShiftData
// (GET /api/shift). The band chart reads GET /api/band/imu/recent, whose times are in seconds
// (Python SDK); this file converts them to the JavaScript SDK's milliseconds. When the server is
// not running, the dashboard shows the demo data and says so.
import { useEffect, useRef, useState } from 'react';
import { windowRms } from '../band/motion';
import type { MotionSample, ShiftData } from '../data/types';

export const API = import.meta.env.VITE_BELTGUARD_API ?? 'http://127.0.0.1:8010';
const SHIFT_EVERY_MS = 3000;
const IMU_EVERY_MS = 500;

export type DataMode = 'connecting' | 'server' | 'demo';

interface ImuRecent {
  samples: Array<{ n: number | null; t: number; time: number; ax: number; ay: number; az: number; gx: number | null; gy: number | null; gz: number | null }>;
  source: 'band' | 'replay' | 'synthetic';
  configured_hz: number | null;
  effective_hz: number | null;
  stale: boolean;
}

async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const r = await fetch(API + path, { signal });
  if (!r.ok) throw new Error(`${path}: status ${r.status}`);
  return (await r.json()) as T;
}

/** Python SDK seconds -> JavaScript SDK milliseconds. */
function toMotion(s: ImuRecent['samples'][number]): MotionSample {
  return { ...s, t: Math.round(s.t * 1000), time: Math.round(s.time * 1000) };
}

export function useShift(demo: ShiftData): { data: ShiftData; mode: DataMode; error: string | null } {
  const [mode, setMode] = useState<DataMode>('connecting');
  const [server, setServer] = useState<ShiftData | null>(null);
  const [imu, setImu] = useState<ImuRecent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    const ctl = new AbortController();
    let failures = 0;
    const pollShift = async () => {
      try {
        const s = await getJson<ShiftData>('/api/shift', ctl.signal);
        if (!alive.current) return;
        failures = 0;
        setServer(s);
        setMode('server');
        setError(null);
      } catch (e) {
        if (!alive.current || ctl.signal.aborted) return;
        failures += 1;
        if (failures >= 2 || mode === 'connecting') {
          setMode('demo');
          setServer(null);
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    };
    void pollShift();
    const id = setInterval(pollShift, SHIFT_EVERY_MS);
    return () => { alive.current = false; ctl.abort(); clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (mode !== 'server') return;
    const ctl = new AbortController();
    const poll = async () => {
      try {
        setImu(await getJson<ImuRecent>('/api/band/imu/recent?seconds=5', ctl.signal));
      } catch {
        /* the band panel shows the server's band status; a missed poll is not an error */
      }
    };
    void poll();
    const id = setInterval(poll, IMU_EVERY_MS);
    return () => { ctl.abort(); clearInterval(id); };
  }, [mode]);

  if (mode !== 'server' || !server) return { data: demo, mode, error };

  const samples = imu ? imu.samples.map(toMotion) : [];
  const rms = samples.length ? windowRms(samples) : { accelRms: null, gyroRms: null };
  const replay = Boolean(server.replay || server.band?.replay || (imu && imu.source !== 'band'));
  const data: ShiftData = {
    ...server,
    replay,
    band: {
      ...server.band,
      source: 'backend',
      replay,
      samples,
      ...rms,
      live: server.band.live && !(imu?.stale ?? true),
      effectiveRate: imu?.effective_hz ?? server.band.effectiveRate ?? null,
    },
  };
  return { data, mode, error };
}
