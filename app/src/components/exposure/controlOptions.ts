// Control options for the RecommendationCard "Review & Plan" panel.
// All maths goes through lib/hav (havPoints / exposureLevel); nothing is reimplemented here.
import { havPoints, exposureLevel, type ExposureLevel } from '../../lib/hav';
import type { Exposure, Recommendation, Tool } from '../../data/types';

export type ControlId = 'low-vib-tool' | 'rotation' | 'split';

export interface ControlOption {
  id: ControlId;
  title: string;
  detail: string;
  optionPts: number; // this worker's points from the next task under this control
  total: number;     // current + optionPts
  level: ExposureLevel;
  best: boolean;
}

/** Vibration emission assumed for a low-vibration replacement tool, m/s². */
export const LOW_VIB_AHV = 6;

/**
 * @param crewSize workers currently on the task (e.g. shift.workers on-task count). Split divides the
 * trigger time across this crew; rotation brings in one extra fitter from another crew on top of it.
 */
export function buildControlOptions(
  exposure: Exposure,
  recommendation: Recommendation,
  tool: Tool,
  crewSize: number,
): ControlOption[] {
  // Guard: without a positive emission the trigger time cannot be backed out, so offer nothing.
  if (!(tool.aHv > 0)) return [];

  // Back out the trigger time of the planned task from its projected contribution:
  // nextPts = projected − current; minutes = nextPts / (points per minute on this tool).
  const nextPts = Math.max(0, recommendation.projectedPoints - exposure.current);
  const minutes = nextPts / havPoints(tool.aHv, 1);
  const mins = (m: number) => `~${Math.max(1, Math.round(m))} min`;
  const crew = Math.max(1, Math.floor(crewSize));
  const rotated = crew + 1; // this crew plus one fitter borrowed from another crew

  const raw: { id: ControlId; title: string; detail: string; optionPts: number }[] = [
    {
      id: 'low-vib-tool',
      title: 'Lower-vibration tool',
      detail: `${LOW_VIB_AHV} m/s² tool instead of ${tool.aHv} m/s², ${mins(minutes)}`,
      optionPts: havPoints(LOW_VIB_AHV, minutes),
    },
    {
      id: 'rotation',
      title: 'Rotate with another crew',
      detail: `Alternate with 1 extra fitter from another crew, ${mins(minutes / rotated)} each`,
      optionPts: havPoints(tool.aHv, minutes / rotated),
    },
  ];
  if (crew > 1) {
    raw.push({
      id: 'split',
      title: `Split across ${crew} workers`,
      detail: `Share the idler swap within this crew, ${mins(minutes / crew)} each`,
      optionPts: havPoints(tool.aHv, minutes / crew),
    });
  }

  const lowest = Math.min(...raw.map((r) => r.optionPts));
  return raw.map((r) => {
    // Round before classifying so the shown number and its level always agree.
    const optionPts = Math.round(r.optionPts);
    const total = Math.round(exposure.current + r.optionPts);
    return { ...r, optionPts, total, level: exposureLevel(total), best: r.optionPts === lowest };
  });
}
