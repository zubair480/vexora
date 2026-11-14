// Shared tone helpers for the exposure section (KpiRow, ExposureGauge, RecommendationCard).
import type { Tone } from '../ui/primitives';
import type { ExposureLevel } from '../../lib/hav';

export const levelTone: Record<ExposureLevel, Tone> = { below: 'ok', action: 'warn', limit: 'bad' };

export const levelLabel: Record<ExposureLevel, string> = {
  below: 'Below action value',
  action: 'Above action value',
  limit: 'Above limit value',
};

/** Light tone colours. Use for fills, strokes and icon glyphs only, not for text (too low contrast on white). */
export const toneText: Record<Tone, string> = {
  ok: 'text-ok',
  warn: 'text-warn',
  bad: 'text-bad',
  info: 'text-info',
  sensor: 'text-sensor',
  plum: 'text-plum',
  brand: 'text-brand',
  neutral: 'text-ink',
};

/**
 * Readable toned text: the tone mixed toward ink so it clears 4.5:1 on white and on the tinted -bg fills.
 * Built from the tokens (no hex) so it follows the theme. Use for every piece of toned text.
 */
export const toneTextStrong: Record<Tone, string> = {
  ok: 'text-[color:color-mix(in_srgb,rgb(var(--ok))_55%,rgb(var(--ink)))]',
  warn: 'text-[color:color-mix(in_srgb,rgb(var(--warn))_55%,rgb(var(--ink)))]',
  bad: 'text-[color:color-mix(in_srgb,rgb(var(--bad))_70%,rgb(var(--ink)))]',
  brand: 'text-[color:color-mix(in_srgb,rgb(var(--brand))_55%,rgb(var(--ink)))]',
  info: 'text-info',
  sensor: 'text-sensor',
  plum: 'text-plum',
  neutral: 'text-ink',
};

/** Focus ring that stays visible on white and on the gold primary button. */
export const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/70 focus-visible:ring-offset-2 focus-visible:ring-offset-surface';
