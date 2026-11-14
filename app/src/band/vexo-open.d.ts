// Types for the part of the kit's JavaScript SDK this app uses. The SDK itself is plain JS in
// reference/vexo-open-kit/js/src (imported through the `vexo-open` alias in vite.config.ts).
// Names and units follow reference/vexo-open-kit/docs/api.md.
declare module 'vexo-open' {
  export type TransportKind = 'ble' | 'serial' | 'sim';

  export interface ConnectOptions {
    transport?: TransportKind | SimTransport;
    autoReconnect?: boolean;
    reconnectSeconds?: number;
    timeoutMs?: number;
  }

  export interface ConfigureOptions {
    motion?: number | boolean | 'fastest';
    sensors?: 'accel' | 'gyro' | 'both';
    events?: boolean;
    activity?: boolean | number | 'fastest';
    battery?: boolean | number | 'fastest';
    temperature?: boolean | number | 'fastest';
    link?: boolean | number | 'fastest';
    micLevel?: boolean | number | 'fastest';
    audio?: boolean;
  }

  export interface BandInfo {
    number: number;
    protocol: '1.0' | '1.1';
    firmware: string;
    features: string[];
    capabilities: string[];
  }

  export interface MotionEvent {
    t: number; time: number; n: number | null;
    ax: number; ay: number; az: number;
    gx: number | null; gy: number | null; gz: number | null;
  }
  export interface BatteryEvent { time: number; percent: number; millivolts: number; state: 'battery' | 'charging' | 'charged'; charging: boolean }
  export interface LinkEvent { t: number; time: number; rssi: number | null; txPower: number; phy: string }
  export interface GestureEvent { t: number; time: number; kind: string; arg: number; face?: string }
  export interface DisconnectEvent { reason: string; reconnect?: string }

  export class Band {
    static connect(number: number | null, options?: ConnectOptions): Promise<Band>;
    readonly number: number;
    readonly name: string;
    readonly connected: boolean;
    readonly reconnecting: boolean;
    readonly transportKind: TransportKind;
    readonly info: BandInfo;
    on(type: 'motion', handler: (e: MotionEvent) => void): void;
    on(type: 'battery', handler: (e: BatteryEvent) => void): void;
    on(type: 'link', handler: (e: LinkEvent) => void): void;
    on(type: 'event', handler: (e: GestureEvent) => void): void;
    on(type: 'disconnect', handler: (e: DisconnectEvent) => void): void;
    on(type: 'reconnecting' | 'reconnected', handler: (e: unknown) => void): void;
    on(type: string, handler: (e: unknown) => void): void;
    off(type: string, handler: (e: never) => void): void;
    configure(options: ConfigureOptions): Promise<unknown>;
    identify(): Promise<void>;
    disconnect(): Promise<void>;
  }

  export class SimTransport {
    constructor(number: number, options?: { firmware?: '1.0' | '1.1'; autoTap?: boolean });
    tap(kind: string): void;
    playMotion(fn: (s: number) => Partial<Record<'ax' | 'ay' | 'az' | 'gx' | 'gy' | 'gz', number>>, options?: { seconds?: number }): void;
  }

  export class VexoError extends Error {
    code: string;
  }

  export function bandName(number: number): string;
}
