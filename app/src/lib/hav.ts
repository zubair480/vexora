// HAV exposure points, UK HSE points system: points = 2 × a_hv² × hours.
// 2.5 m/s² for 8 hours = 100 points (action value); 5 m/s² for 8 hours = 400 (limit value).
export const ACTION_VALUE = 100;
export const LIMIT_VALUE = 400;

export function havPoints(aHv: number, minutes: number): number {
  return 2 * aHv * aHv * (minutes / 60);
}

export type ExposureLevel = 'below' | 'action' | 'limit';

export function exposureLevel(points: number): ExposureLevel {
  if (points >= LIMIT_VALUE) return 'limit';
  if (points >= ACTION_VALUE) return 'action';
  return 'below';
}
