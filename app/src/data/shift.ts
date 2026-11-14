// Mock shift data. No backend and no band connection yet (see design/BRIEF.md, "Data").
import { ACTION_VALUE, LIMIT_VALUE, havPoints } from '../lib/hav';
import type { SensorSample, ShiftData } from './types';

function sensorWindow(n = 120): SensorSample[] {
  // Deterministic pseudo-noise so the charts look alive but never change between renders.
  const out: SensorSample[] = [];
  for (let i = 0; i < n; i++) {
    const t = i * 0.5;
    const accel = 1.2 * Math.sin(i * 0.9) * Math.cos(i * 0.31) + 0.35 * Math.sin(i * 2.7);
    const gyro = 38 * Math.sin(i * 0.7 + 1) * Math.cos(i * 0.23) + 12 * Math.sin(i * 3.1);
    out.push({ t, accel: +accel.toFixed(2), gyro: +gyro.toFixed(1) });
  }
  return out;
}

const tool = {
  id: 'tool-iw01',
  name: 'Impact Wrench 01',
  type: 'Impact wrench',
  aHv: 12.5,
  minutesToday: 18,
  inUse: true,
};

// Points so far: earlier tasks plus the impact wrench. Projection: the planned idler swap
// (another ~10 minutes on the same wrench).
const current = 85;
const projected = Math.ceil(current + havPoints(tool.aHv, 10));   // 85 + 52.1 → 138

export const shift: ShiftData = {
  conveyor: {
    id: 'CV-04',
    name: 'Main Overland Conveyor',
    site: 'North Ridge Mine',
    pit: 'North Pit',
    lengthM: 1830,
    idlers: 146,
    inService: true,
    beltHealth: 96,
    stations: [
      { id: 's1', index: 1, name: 'Station 1', role: 'Head / Drive', status: 'ok', position: 0.06, asset: 'Head Pulley', note: 'Inspected 08:06 · OK' },
      { id: 's2', index: 2, name: 'Station 2', role: 'Carry Idlers', status: 'ok', position: 0.28, asset: 'Loading Zone', note: 'Inspected 08:20 · OK' },
      { id: 's3', index: 3, name: 'Station 3', role: 'Return Idlers', status: 'defect', position: 0.5, asset: 'Return Idler 14', note: 'Elevated vibration · likely bearing wear', vibrationDelta: 78 },
      { id: 's4', index: 4, name: 'Station 4', role: 'Transition', status: 'ok', position: 0.72, asset: 'Transition Idlers', note: 'Last checked yesterday · OK' },
      { id: 's5', index: 5, name: 'Station 5', role: 'Tail / Take-up', status: 'ok', position: 0.94, asset: 'Tail Pulley', note: 'Last checked yesterday · OK' },
    ],
  },
  shift: { label: 'Day', start: '06:00', end: '18:00' },
  workers: [
    { id: 'w1', name: 'Liam Carter', role: 'Maintenance Technician', initials: 'LC', onTask: true, exposurePoints: 45 },
    { id: 'w2', name: 'Emma Ruiz', role: 'Mechanical Fitter', initials: 'ER', onTask: true, exposurePoints: 40 },
  ],
  tool,
  exposure: { current, projected, action: ACTION_VALUE, limit: LIMIT_VALUE },
  defect: {
    id: 'd1',
    title: 'Return idler bearing seized, needs replacement',
    asset: 'Return Idler 14',
    stationId: 's3',
    severity: 'high',
    detail: 'Bearing is seized (no rotation), with heat and wear observed. Replace the idler and bearing assembly.',
    detectedAt: '10:24',
  },
  workOrder: {
    id: 'WO-4783',
    status: 'draft',
    asset: 'CV-04 — Station 3 / Return Idler 14',
    task: 'Replace return idler (idler + bearing)',
    priority: 'high',
    planned: 'Today, 14:00 (≈ 1.5 h)',
    assignee: 'Maintenance Crew A',
  },
  recommendation: {
    headline: `Next planned repair could raise exposure to ${projected} points — review required.`,
    detail: `Based on work order WO-4783 and Impact Wrench 01, estimated daily HAV exposure would rise from ${current} to ${projected} points (+${projected - current}). That exceeds the ${ACTION_VALUE}-point action value. Review the work method, tool or controls before proceeding.`,
    projectedPoints: projected,
    deltaPoints: projected - current,
    primaryCta: 'Review & Plan',
    secondaryCta: 'View Details',
  },
  band: {
    name: 'Vexo band',
    connected: true,
    live: true,
    battery: 92,
    accelRms: 1.24,
    gyroRms: 37.6,
    samples: sensorWindow(),
  },
  events: [
    { id: 'e1', kind: 'walking', start: '08:02', end: '08:06', title: 'Walking', detail: 'To Head Pulley' },
    { id: 'e2', kind: 'inspection', start: '08:06', end: '08:14', title: 'Inspection', detail: 'Head Pulley (OK)' },
    { id: 'e3', kind: 'walking', start: '08:14', end: '08:20', title: 'Walking', detail: 'To Loading Zone' },
    { id: 'e4', kind: 'inspection', start: '08:20', end: '08:27', title: 'Inspection', detail: 'Loading Zone (OK)' },
    { id: 'e5', kind: 'vibration', start: '09:26', title: 'Vibration increase', detail: 'Return Idler 14 · +42%' },
    { id: 'e6', kind: 'vibration', start: '09:58', title: 'Vibration elevated', detail: 'Now +78% vs baseline' },
    { id: 'e7', kind: 'defect', start: '10:24', end: '10:31', title: 'Defect flagged', detail: 'Return Idler 14 (bearing seized)' },
    { id: 'e8', kind: 'tool', start: '10:38', end: '10:56', title: 'Tool use', detail: 'Impact Wrench 01 (18 min)' },
    { id: 'e9', kind: 'exposure', start: '10:56', title: 'HAV exposure updated', detail: `Current exposure: ${current} pts` },
    { id: 'e10', kind: 'workorder', start: '10:58', end: '11:00', title: 'Work order', detail: 'WO-4783 (Draft)' },
  ],
  kpis: {
    beltHealth: 96,
    activeDefects: 1,
    workersOnTask: 2,
    vibrationRisk: 'Elevated',
    exposurePoints: current,
  },
};
