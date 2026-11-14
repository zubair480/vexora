// React hook over the Vexo SDK. Gives the dashboard one BandState, whatever the source:
//   'mock'  canned data from data/shift.ts, no SDK (default)
//   'sim'   the SDK's pretend band: real protocol, made-up motion
//   'ble'   the team's real band over Bluetooth (Chrome or Edge, needs a click)
//   'serial' the team's real band on the USB cable
// It only ever connects to BAND_NUMBER (the SDK checks the number and fails with `wrong-band`).
import { useCallback, useEffect, useRef, useState } from 'react';
import { Band, type MotionEvent } from 'vexo-open';
import type { BandSource, BandState, GestureKind, MotionSample } from '../data/types';
import { BAND_NUMBER, MOTION_RATE, STALE_MS, WINDOW_SECONDS } from './config';
import { windowRms } from './motion';

const GESTURES: GestureKind[] = ['tap', 'double-tap', 'free-fall', 'tilt', 'orientation', 'motion-start', 'motion-stop'];

export interface VexoBand {
  state: BandState;
  connect: (source: Exclude<BandSource, 'mock' | 'backend'>) => Promise<void>;
  disconnect: () => Promise<void>;
  identify: () => Promise<void>;
}

export function useVexoBand(mock: BandState): VexoBand {
  const [state, setState] = useState<BandState>(mock);
  const band = useRef<Band | null>(null);
  const buf = useRef<MotionSample[]>([]);
  const lastSampleAt = useRef(0);

  // Push the motion buffer into React state a few times a second, not on every sample.
  useEffect(() => {
    if (state.source === 'mock') return;
    const id = setInterval(() => {
      const samples = buf.current.slice();
      const live = Date.now() - lastSampleAt.current < STALE_MS;
      setState((s) => ({ ...s, samples, live, reconnecting: band.current?.reconnecting ?? false, ...windowRms(samples) }));
    }, 200);
    return () => clearInterval(id);
  }, [state.source]);

  const disconnect = useCallback(async () => {
    const b = band.current;
    band.current = null;
    buf.current = [];
    if (b) await b.disconnect().catch(() => {});
    setState(mock);
  }, [mock]);

  const connect = useCallback(async (source: Exclude<BandSource, 'mock' | 'backend'>) => {
    if (BAND_NUMBER === null) {
      setState((s) => ({ ...s, error: 'Set your band number first: BAND_NUMBER in app/src/band/config.ts (it is written on the band\'s bag).' }));
      return;
    }
    await disconnect();
    try {
      const b = await Band.connect(BAND_NUMBER, { transport: source, autoReconnect: true });
      band.current = b;
      const keep = MOTION_RATE * WINDOW_SECONDS;
      b.on('motion', (e: MotionEvent) => {
        buf.current.push({ t: e.t, time: e.time, n: e.n, ax: e.ax, ay: e.ay, az: e.az, gx: e.gx, gy: e.gy, gz: e.gz });
        if (buf.current.length > keep) buf.current.splice(0, buf.current.length - keep);
        lastSampleAt.current = Date.now();
      });
      b.on('battery', (e) => setState((s) => ({ ...s, battery: { percent: e.percent, state: e.state, charging: e.charging } })));
      b.on('link', (e) => setState((s) => ({ ...s, rssi: e.rssi })));
      b.on('event', (e) => {
        if ((GESTURES as string[]).includes(e.kind)) setState((s) => ({ ...s, lastGesture: { kind: e.kind as GestureKind, time: e.time } }));
      });
      b.on('disconnect', () => setState((s) => ({ ...s, connected: false, live: false })));
      b.on('reconnected', () => setState((s) => ({ ...s, connected: true })));
      await b.configure({ motion: MOTION_RATE, events: true, battery: true, ...(source === 'ble' || source === 'sim' ? { link: true } : {}) });
      setState({
        source,
        number: b.number,
        name: b.name,
        connected: true,
        reconnecting: false,
        live: true,
        firmware: b.info?.firmware ?? null,
        motionRate: MOTION_RATE,
        battery: null,
        rssi: null,
        lastGesture: null,
        samples: [],
        accelRms: 0,
        gyroRms: 0,
        error: null,
      });
    } catch (e) {
      band.current = null;
      setState({ ...mock, error: e instanceof Error ? e.message : String(e) });   // SDK messages say what to do next
    }
  }, [disconnect, mock]);

  const identify = useCallback(async () => {
    await band.current?.identify().catch(() => {});
  }, []);

  useEffect(() => () => { band.current?.disconnect().catch(() => {}); }, []);

  return { state, connect, disconnect, identify };
}
