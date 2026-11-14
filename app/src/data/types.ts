// Shared data model for the BeltGuard dashboard. Every section reads from these types.
//
// Two sources fill it: the demo data in data/shift.ts, and the BeltGuard server's GET /api/shift
// (backend/shift_view.py). The server never invents a value: anything it has no real source for
// is `null`, and every component must show "—" (or hide that detail) for a null. Nothing in the UI
// may fill a null with a made-up number. A whole section can be null too (no tool used yet, no
// defect, no work order, no recommendation): show a calm empty state.

export type Status = 'ok' | 'attention' | 'defect';

export interface Station {
  id: string;
  index: number;                    // 1..5, left to right
  name: string;                     // "Station 3"
  role: string | null;              // "Return Idlers"
  status: Status;
  position: number;                 // 0..1 along the belt
  asset?: string | null;            // "Return Idler 14"
  note?: string | null;             // tooltip / callout text
  vibrationDelta?: number | null;   // % vs baseline; null = no asset vibration sensor
  inspected?: boolean;              // inspected this shift (server)
}

export interface Conveyor {
  id: string;                       // "CV-04"
  name: string | null;              // "Main Overland Conveyor"
  site: string | null;
  pit: string | null;
  lengthM: number | null;
  idlers: number | null;
  inService: boolean | null;
  beltHealth: number | null;        // 0..100; null = not measured
  stations: Station[];
}

export interface Shift {
  label: string | null;             // "Day"
  start: string | null;             // "06:00"
  end: string | null;               // "18:00"
}

export interface Worker {
  id: string;
  name: string;
  role: string | null;
  initials: string;
  onTask: boolean;
  exposurePoints: number | null;    // null = not measured (only the band wearer is)
}

export interface Tool {
  id: string;
  name: string;                     // "Impact Wrench 01"
  type: string | null;              // "Impact wrench"
  aHv: number | null;               // vibration emission, m/s²
  aHvSource?: string | null;        // where aHv comes from (manufacturer, measured, ...)
  minutesToday: number;
  inUse: boolean;
  confirmation?: string | null;     // tool session confirmed / unconfirmed / rejected (server)
  stationId?: string | null;        // station of the work open while the tool ran (server, derived)
  workOrderId?: string | null;      // work order open while the tool ran (server, derived)
}

export interface Exposure {
  current: number | null;           // points so far this shift; null = unavailable
  projected: number | null;         // points if the next planned task goes ahead
  action: number;                   // 100
  limit: number;                    // 400
  label?: string;                   // "Estimated HAV exposure — screening/planning, not certified measurement"
  status?: string;                  // server: 'ok' | 'unavailable' | ...
  reasons?: string[];               // why a value is missing or estimated
}

export interface Defect {
  id: string;
  title: string;
  asset: string | null;
  stationId: string | null;
  severity: 'low' | 'medium' | 'high' | string | null;
  detail: string;
  detectedAt: string | null;        // "10:24"
  verificationStatus?: string | null;   // 'unverified' until a person reviews it
  source?: string | null;
}

export interface WorkOrder {
  id: string;                       // "WO-4783"
  status: 'draft' | 'submitted' | 'approved' | string;
  asset: string;
  task: string;
  priority: 'low' | 'medium' | 'high' | string;
  planned: string | null;
  assignee: string | null;
}

export interface RecommendationOption {
  [key: string]: unknown;           // server planner options; render label/points when present
}

export interface Recommendation {
  headline: string;
  detail: string;
  projectedPoints: number | null;
  deltaPoints: number | null;
  primaryCta: string;
  secondaryCta: string;
  options?: RecommendationOption[];
  requiresApproval?: boolean;
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
  state: 'battery' | 'charging' | 'charged' | null;
  charging: boolean;
}

/** SDK gesture kinds (`configure({ events: true })`). */
export type GestureKind = 'tap' | 'double-tap' | 'free-fall' | 'tilt' | 'orientation' | 'motion-start' | 'motion-stop';

/** Where the band data comes from. 'mock' = demo data; 'backend' = the BeltGuard server owns the
 *  band link (the normal case); 'sim' | 'ble' | 'serial' = SDK in this browser (fallback only). */
export type BandSource = 'mock' | 'backend' | 'sim' | 'ble' | 'serial';

export interface BandState {
  source: BandSource;
  replay?: boolean;         // server is replaying a recording: label it, never present it as live
  number: number | null;    // the team's band number (SDK `band.number`); null = not set yet
  name: string | null;      // SDK `band.name`, e.g. "Vexo-HACK-07"
  connected: boolean;       // SDK `band.connected`
  reconnecting: boolean;    // SDK `band.reconnecting`
  live: boolean;            // false = no motion sample for a while (stale)
  firmware: string | null;  // SDK `band.info.firmware`
  motionRate: number | null;     // samples/second asked for
  effectiveRate?: number | null; // samples/second actually arriving (server)
  completeness?: number | null;  // 0..1 share of samples received (server)
  battery: BandBattery | null;
  rssi: number | null;      // SDK `link` event, dBm; null over USB or with no reading
  lastGesture: { kind: GestureKind; time: number } | null;
  samples: MotionSample[];  // newest last, a few seconds' window
  accelRms: number | null;  // m/s², vibration around 1 g, over the window (derived)
  gyroRms: number | null;   // °/s, over the window (derived)
  error: string | null;     // SDK / server message, shown to the user as is
}

export type EventKind = 'walking' | 'inspection' | 'tool' | 'workorder' | 'defect' | 'exposure' | 'vibration';

export interface ShiftEvent {
  id: string;
  kind: EventKind;
  start: string | null;     // "08:02"
  end?: string | null;      // "08:06"
  title: string;
  detail: string;
  source?: string;          // server provenance: band | replay | synthetic | manual | ai | derived
  stationId?: string | null;
  linkedWorkOrderId?: string | null;
  link?: string | null;     // e.g. "derived: open work order"
}

export interface Kpis {
  beltHealth: number | null;
  activeDefects: number;
  workersOnTask: number | null;
  vibrationRisk: 'Normal' | 'Elevated' | 'High' | null;
  exposurePoints: number | null;
}

export interface ShiftData {
  replay?: boolean;         // any part comes from a replay / synthetic data: show a visible label
  conveyor: Conveyor;
  shift: Shift;
  workers: Worker[];
  tool: Tool | null;
  exposure: Exposure;
  defect: Defect | null;
  workOrder: WorkOrder | null;
  recommendation: Recommendation | null;
  band: BandState;
  events: ShiftEvent[];
  kpis: Kpis;
}
