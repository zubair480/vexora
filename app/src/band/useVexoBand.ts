// React hook over the Vexo SDK: the browser owns the band link, exactly as the kit's own dashboard
// does (reference/vexo-open-kit/dashboard/app.js, connect() and setUp()), and forwards every motion
// sample to the BeltGuard server so ToolSense, exposure and the planner run on real data.
//   'mock'  canned data from data/shift.ts, no SDK (before anyone connects)
//   'ble'   the team's band over Bluetooth (Chrome or Edge, needs a click)
//   'serial' the team's band on the USB cable
//   'sim'   the SDK's pretend band (testing only; not forwarded to the server)
// It only ever connects to BAND_NUMBER (the SDK checks the number and fails with `wrong-band`).
import { useCallback, useEffect, useRef, useState } from 'react';
import { Band, type ConfigureOptions, type MotionEvent } from 'vexo-open';
import { API } from '../api/useShift';
import type { BandSource, BandState, GestureKind, MotionSample } from '../data/types';
import { BAND_NUMBER, MOTION_RATE, STALE_MS, WINDOW_SECONDS } from './config';
import { windowRms } from './motion';

const GESTURES: GestureKind[] = ['tap', 'double-tap', 'free-fall', 'tilt', 'orientation', 'motion-start', 'motion-stop'];
const FORWARD_EVERY_MS = 200;

/** The kit dashboard's startup streams (dashboard/logic.js, startupStreams), turned on one by one. */
function startupStreams(kind: 'ble' | 'serial' | 'sim'): Array<[string, ConfigureOptions]> {
  const list: Array<[string, ConfigureOptions]> = [
    ['motion', { motion: MOTION_RATE }],
    ['events', { events: true }],
    ['battery', { battery: true }],
    ['temperature', { temperature: true }],
    ['steps', { activity: true }],
  ];
  if (kind !== 'serial') list.push(['signal', { link: true }]);   // signal strength is Bluetooth only
  return list;
}

export type ConnectPhase = 'idle' | 'connecting' | 'setting-up' | 'connected';

export interface VexoBand {
  state: BandState;
  phase: ConnectPhase;
  forwarding: 'off' | 'on' | 'refused';   // is the BeltGuard server receiving the samples?
  connect: (source: Exclude<BandSource, 'mock' | 'backend'>) => Promise<void>;
  disconnect: () => Promise<void>;
  identify: () => Promise<void>;
}

function post(path: string, body: unknown): Promise<Response> {
  return fetch(API + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

export function useVexoBand(mock: BandState): VexoBand {
  const [state, setState] = useState<BandState>(mock);
  const [phase, setPhase] = useState<ConnectPhase>('idle');
  const [forwarding, setForwarding] = useState<'off' | 'on' | 'refused'>('off');
  const band = useRef<Band | null>(null);
  const buf = useRef<MotionSample[]>([]);        // chart window
  const outbox = useRef<MotionSample[]>([]);     // samples not yet sent to the server
  const battery = useRef<BandState['battery']>(null);
  const lastSampleAt = useRef(0);
  const forwardOn = useRef(false);

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

  // Forward the SDK motion events, unchanged (JS units: ms), to the server every ~200 ms.
  useEffect(() => {
    if (forwarding !== 'on') return;
    const id = setInterval(() => {
      if (!forwardOn.current || outbox.current.length === 0) return;
      const samples = outbox.current.splice(0, outbox.current.length);
      post('/api/band/browser/samples', { samples, battery: battery.current ? { percent: battery.current.percent, state: battery.current.state } : null })
        .catch(() => {});   // a missed batch only leaves a gap the server reports as missing samples
    }, FORWARD_EVERY_MS);
    return () => clearInterval(id);
  }, [forwarding]);

  const sayBye = useCallback(() => {
    if (!forwardOn.current) return;
    forwardOn.current = false;
    setForwarding('off');
    // sendBeacon survives a closing tab; fall back to fetch.
    const ok = navigator.sendBeacon?.(API + '/api/band/browser/bye', new Blob(['{}'], { type: 'application/json' }));
    if (!ok) post('/api/band/browser/bye', {}).catch(() => {});
  }, []);

  useEffect(() => {
    window.addEventListener('pagehide', sayBye);
    return () => window.removeEventListener('pagehide', sayBye);
  }, [sayBye]);

  const disconnect = useCallback(async () => {
    sayBye();
    const b = band.current;
    band.current = null;
    buf.current = [];
    outbox.current = [];
    if (b) await b.disconnect().catch(() => {});
    setPhase('idle');
    setState(mock);
  }, [mock, sayBye]);

  const connect = useCallback(async (source: Exclude<BandSource, 'mock' | 'backend'>) => {
    if (BAND_NUMBER === null) {
      setState((s) => ({ ...s, error: 'Set your band number first: BAND_NUMBER in app/src/band/config.ts (it is written on the band\'s bag).' }));
      return;
    }
    await disconnect();
    setPhase('connecting');
    setState((s) => ({ ...s, error: null }));
    let b: Band;
    try {
      // Same call as the kit dashboard: the chooser lists only this band's exact name and service.
      b = await Band.connect(BAND_NUMBER, { transport: source, autoReconnect: true });
    } catch (e) {
      band.current = null;
      setPhase('idle');
      setState({ ...mock, error: e instanceof Error ? e.message : String(e) });   // SDK messages say what to do next
      return;
    }
    band.current = b;
    setPhase('setting-up');
    const keep = MOTION_RATE * WINDOW_SECONDS;
    b.on('motion', (e: MotionEvent) => {
      const s: MotionSample = { t: e.t, time: e.time, n: e.n, ax: e.ax, ay: e.ay, az: e.az, gx: e.gx, gy: e.gy, gz: e.gz };
      buf.current.push(s);
      if (buf.current.length > keep) buf.current.splice(0, buf.current.length - keep);
      if (forwardOn.current) outbox.current.push(s);
      lastSampleAt.current = Date.now();
    });
    b.on('battery', (e) => {
      battery.current = { percent: e.percent, state: e.state, charging: e.charging };
      setState((s) => ({ ...s, battery: battery.current }));
    });
    b.on('link', (e) => setState((s) => ({ ...s, rssi: e.rssi })));
    b.on('event', (e) => {
      if ((GESTURES as string[]).includes(e.kind)) setState((s) => ({ ...s, lastGesture: { kind: e.kind as GestureKind, time: e.time } }));
    });
    b.on('reconnecting', () => setState((s) => ({ ...s, reconnecting: true })));
    b.on('reconnected', () => setState((s) => ({ ...s, connected: true, reconnecting: false })));
    b.on('disconnect', (e) => {
      if (band.current !== b) return;
      sayBye();
      band.current = null;
      setPhase('idle');
      setState((s) => ({ ...s, connected: false, live: false, reconnecting: false, error: e.reason === 'lost' ? 'The band went out of reach. Double-tap it and press Connect again.' : s.error }));
    });

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

    // Kit dashboard setUp(): flash the band, then turn the streams on one by one.
    await b.identify().catch(() => {});
    const failed: string[] = [];
    for (const [label, streams] of startupStreams(source)) {
      if (band.current !== b) return;
      try {
        await b.configure(streams);
      } catch {
        failed.push(label);
      }
    }
    if (band.current !== b) return;
    setPhase('connected');
    if (failed.length) setState((s) => ({ ...s, error: `Connected, but the band did not start: ${failed.join(', ')}.` }));

    // Tell the BeltGuard server a real band is streaming (not for the pretend band).
    if (source === 'sim') return;
    try {
      const r = await post('/api/band/browser/hello', {
        band_name: b.name,
        band_number: b.number,
        firmware: b.info?.firmware ?? null,
        motion_hz: MOTION_RATE,
        battery: battery.current ? { percent: battery.current.percent, state: battery.current.state } : null,
      });
      if (r.ok) {
        outbox.current = [];
        forwardOn.current = true;
        setForwarding('on');
      } else {
        setForwarding('refused');
        const why = r.status === 403 ? 'this is not the team\'s band number' : r.status === 409 ? 'a replay is running on the server' : `status ${r.status}`;
        setState((s) => ({ ...s, error: `Band connected, but the BeltGuard server refused the readings: ${why}.` }));
      }
    } catch {
      setForwarding('refused');
      setState((s) => ({ ...s, error: 'Band connected, but the BeltGuard server is not running, so readings are only shown here (start it on port 8010).' }));
    }
  }, [disconnect, mock, sayBye]);

  const identify = useCallback(async () => {
    await band.current?.identify().catch(() => {});
  }, []);

  useEffect(() => () => { band.current?.disconnect().catch(() => {}); }, []);

  return { state, phase, forwarding, connect, disconnect, identify };
}
