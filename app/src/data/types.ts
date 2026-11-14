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

export interface SensorSample {
  t: number;                // seconds since start of window
  accel: number;            // |a| deviation, m/s²
  gyro: number;             // °/s
}

export interface BandState {
  name: string;             // "Vexo band"
  connected: boolean;
  live: boolean;            // false = stale data
  battery: number;          // %
  accelRms: number;
  gyroRms: number;
  samples: SensorSample[];
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
