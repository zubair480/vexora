// Helpers over SDK `motion` samples. Units: SDK gives g and °/s; the dashboard shows m/s² and °/s.
import type { MotionSample } from '../data/types';

export const G = 9.80665;

/** Acceleration around 1 g (gravity removed), in m/s². Positive and negative, like a vibration trace. */
export function accelDeviation(s: MotionSample): number {
  return (Math.hypot(s.ax, s.ay, s.az) - 1) * G;
}

/** Turning speed, signed by the strongest axis so the trace looks like a waveform. °/s. */
export function gyroSigned(s: MotionSample): number {
  if (s.gx === null || s.gy === null || s.gz === null) return 0;
  const m = Math.hypot(s.gx, s.gy, s.gz);
  const main = [s.gx, s.gy, s.gz].reduce((a, b) => (Math.abs(b) > Math.abs(a) ? b : a));
  return main < 0 ? -m : m;
}

export function rms(values: number[]): number {
  if (values.length === 0) return 0;
  return Math.sqrt(values.reduce((a, v) => a + v * v, 0) / values.length);
}

export function windowRms(samples: MotionSample[]): { accelRms: number; gyroRms: number } {
  return {
    accelRms: +rms(samples.map(accelDeviation)).toFixed(2),
    gyroRms: +rms(samples.map(gyroSigned)).toFixed(1),
  };
}
