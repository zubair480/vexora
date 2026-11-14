// Normalises the planner options the BeltGuard server sends (backend/planner.py) into something the
// RecommendationCard can render. Only values actually present are used; a missing number stays null.
import type { RecommendationOption } from '../../data/types';

export interface ServerOption {
  key: string;
  title: string;
  detail: string | null;
  additionalPoints: number | null;
  projectedPoints: number | null;
  requiresApproval: boolean;
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v.trim() : null);

/** "review_tool_choice" → "Review tool choice". */
function humanise(kind: string): string {
  const words = kind.replace(/[_-]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function normaliseServerOptions(options: RecommendationOption[]): ServerOption[] {
  return options
    .filter((o): o is RecommendationOption => !!o && typeof o === 'object')
    .map((o, i) => {
      const kind = str(o.kind);
      const title =
        str(o.label) ?? str(o.title) ?? str(o.alt_tool_name) ?? str(o.method) ?? (kind ? humanise(kind) : `Option ${i + 1}`);
      return {
        key: str(o.alternative_id) ?? `${kind ?? 'option'}-${i}`,
        title,
        detail: str(o.description) ?? str(o.detail),
        additionalPoints: num(o.additional_points) ?? num(o.additionalPoints),
        projectedPoints: num(o.projected_points) ?? num(o.projectedPoints),
        requiresApproval: o.requires_approval === true || o.requiresApproval === true,
      };
    });
}
