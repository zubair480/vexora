// KpiRow-private tone helpers. Thresholds live here so the row stays declarative.
import type { Tone } from '../ui/primitives';
import type { Kpis } from '../../data/types';
import { ACTION_VALUE, exposureLevel } from '../../lib/hav';
import { levelLabel, levelTone } from './tone';

/** Icon tile background + glyph colour per tone (glyphs are decorative, so the light tone is fine). */
export const kpiToneTile: Record<Tone, string> = {
  ok: 'bg-ok-bg text-ok',
  warn: 'bg-warn-bg text-warn',
  bad: 'bg-bad-bg text-bad',
  info: 'bg-info-bg text-info',
  sensor: 'bg-sensor-bg text-sensor',
  plum: 'bg-plum-bg text-plum',
  brand: 'bg-brand-bg text-brand',
  neutral: 'bg-panel text-ink-2',
};

export function beltHealthTone(pct: number): { tone: Tone; caption: string } {
  if (pct >= 90) return { tone: 'ok', caption: 'Healthy' };
  if (pct >= 75) return { tone: 'warn', caption: 'Watch' };
  return { tone: 'bad', caption: 'Poor' };
}

export function defectsTone(n: number): { tone: Tone; caption: string } {
  return n > 0 ? { tone: 'bad', caption: 'Needs repair' } : { tone: 'ok', caption: 'None open' };
}

export const riskTone: Record<NonNullable<Kpis['vibrationRisk']>, Tone> = { Normal: 'ok', Elevated: 'warn', High: 'bad' };

export function exposureKpi(points: number): { tone: Tone; caption: string } {
  const lvl = exposureLevel(points);
  const tone = levelTone[lvl];
  if (lvl !== 'below') return { tone, caption: levelLabel[lvl] };
  const left = Math.max(0, Math.round(ACTION_VALUE - points));
  return { tone, caption: `${left} pts below action` };
}
