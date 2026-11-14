// Shared data model for the BeltGuard dashboard. Every section reads from these types.

export type Status = 'ok' | 'attention' | 'defect';

export interface Station {
  id: string;
  index: number;            // 1..5, left to right
  name: string;             // "Station 3"
  role: string;             // "Return Idlers"
  status: Status;
  position: number;         // 0..1 along the belt
  asset?: string;           // "Return Idler 14"
  note?: string;            // tooltip / callout text
  vibrationDelta?: number;  // % vs baseline, e.g. 78
}

export interface Conveyor {
  id: string;               // "CV-04"
  name: string;             // "Main Overland Conveyor"
  site: string;
  pit: string;
  lengthM: number;
  idlers: number;
  inService: boolean;
  beltHealth: number;       // 0..100
  stations: Station[];
}

export interface Shift {
  label: string;            // "Day"
  start: string;            // "06:00"
  end: string;              // "18:00"
}

export interface Worker {
  id: string;
  name: string;
  role: string;
  initials: string;
  onTask: boolean;
  exposurePoints: number;
}

export interface Tool {
  id: string;
  name: string;             // "Impact Wrench 01"
  type: string;             // "Impact wrench"
  aHv: number;              // vibration emission, m/s²
  minutesToday: number;
  inUse: boolean;
}

export interface Exposure {
  current: number;          // points so far this shift
  projected: number;        // points if the next planned task goes ahead
  action: number;           // 100
  limit: number;            // 400
}

export interface Defect {
  id: string;
  title: string;
  asset: string;
  stationId: string;
  severity: 'low' | 'medium' | 'high';
  detail: string;
  detectedAt: string;       // "10:24"
}

export interface WorkOrder {
  id: string;               // "WO-4783"
  status: 'draft' | 'submitted' | 'approved';
  asset: string;
  task: string;
  priority: 'low' | 'medium' | 'high';
  planned: string;
  assignee: string;
}

export interface Recommendation {
  headline: string;
  detail: string;
  projectedPoints: number;
  deltaPoints: number;
  primaryCta: string;
  secondaryCta: string;
}

// ---- Vexo band: these mirror the kit's JavaScript SDK (reference/vexo-open-kit/docs/api.md) ----

/** One SDK `motion` event, unchanged: acceleration in g, turning speed in degrees/second. */
export interface MotionSample {
  t: number;                // band's own counter, ms since it started
  time: number;             // real time, ms since 1970 (JavaScript SDK unit)
  n: number | null;         // sample number (firmware 1.1); null on 1.0
  ax: number; ay: number; az: number;                  // g (null-free: we always ask for accel)
  gx: number | null; gy: number | null; gz: number | null; // °/s; null with sensors: 'accel'
}

/** SDK `battery` event fields. */
export interface BandBattery {
  percent: number;
  state: 'battery' | 'charging' | 'charged';
  charging: boolean;
}

/** SDK gesture kinds (`configure({ events: true })`). */
export type GestureKind = 'tap' | 'double-tap' | 'free-fall' | 'tilt' | 'orientation' | 'motion-start' | 'motion-stop';

/** Where the band data comes from. 'mock' = canned data, no SDK; the rest = SDK transports. */
export type BandSource = 'mock' | 'sim' | 'ble' | 'serial';

export interface BandState {
  source: BandSource;
  number: number | null;    // the team's band number (SDK `band.number`); null = not set yet
  name: string;             // SDK `band.name`, e.g. "Vexo-HACK-07"
  connected: boolean;       // SDK `band.connected`
  reconnecting: boolean;    // SDK `band.reconnecting`
  live: boolean;            // false = no motion sample for a while (stale)
  firmware: string | null;  // SDK `band.info.firmware`
  motionRate: number;       // samples/second asked for in configure({ motion })
  battery: BandBattery | null;
  rssi: number | null;      // SDK `link` event, dBm; null over USB or with no reading
  lastGesture: { kind: GestureKind; time: number } | null;
  samples: MotionSample[];  // newest last, a few seconds' window
  accelRms: number;         // m/s², vibration around 1 g, over the window (derived)
  gyroRms: number;          // °/s, over the window (derived)
  error: string | null;     // SDK VexoError message, shown to the user as is
}

export type EventKind = 'walking' | 'inspection' | 'tool' | 'workorder' | 'defect' | 'exposure' | 'vibration';

export interface ShiftEvent {
  id: string;
  kind: EventKind;
  start: string;            // "08:02"
  end?: string;             // "08:06"
  title: string;
  detail: string;
}

export interface Kpis {
  beltHealth: number;
  activeDefects: number;
  workersOnTask: number;
  vibrationRisk: 'Normal' | 'Elevated' | 'High';
  exposurePoints: number;
}

export interface ShiftData {
  conveyor: Conveyor;
  shift: Shift;
  workers: Worker[];
  tool: Tool;
  exposure: Exposure;
  defect: Defect;
  workOrder: WorkOrder;
  recommendation: Recommendation;
  band: BandState;
  events: ShiftEvent[];
  kpis: Kpis;
}
