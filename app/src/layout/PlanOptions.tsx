// Alternative work plans from the BeltGuard server's optimiser (GET /api/plan/optimise).
// Proposals only: who does which remaining task so nobody's estimated exposure gets too high.
// Nothing is assigned from here; a supervisor decides.
import { ChevronDown, Route, UserX } from 'lucide-react';
import { useEffect, useState } from 'react';
import { API } from '../api/useShift';
import { Card, CardHeader, Pill } from '../components/ui/primitives';

interface Assignment { task_id: string; task_title: string; worker_name: string; additional_points: number; position: number }
interface PlanWorker { worker_id: string; worker_name: string; projected_points: number; current_basis: string }
interface Plan { plan_index: number; assignments: Assignment[]; workers: PlanWorker[]; max_projected_points: number; proven_optimal?: boolean }
interface Optimise {
  plans: Plan[];
  blocked_workers: Array<{ worker_name: string; reason: string }>;
  exposure_cap: number;
  feasible_under_cap: boolean;
  label: string;
}

const REFRESH_MS = 20_000;
const pts = (n: number) => `${Math.round(n)} pts`;

export function PlanOptions({ enabled }: { enabled: boolean }) {
  const [data, setData] = useState<Optimise | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    const ctl = new AbortController();
    const load = async () => {
      try {
        const r = await fetch(API + '/api/plan/optimise', { signal: ctl.signal });
        if (!r.ok) throw new Error(String(r.status));
        setData((await r.json()) as Optimise);
        setFailed(false);
      } catch {
        if (!ctl.signal.aborted) setFailed(true);
      }
    };
    void load();
    const id = setInterval(load, REFRESH_MS);
    return () => { ctl.abort(); clearInterval(id); };
  }, [enabled]);

  if (!enabled || failed || !data || data.plans.length === 0) return null;

  return (
    <Card className="p-5">
      <CardHeader
        icon={<Route size={18} />}
        title="Alternative plans"
        subtitle={`Who does the remaining tasks · cap ${pts(data.exposure_cap)} per worker`}
        right={<Pill tone="plum">Proposal</Pill>}
      />
      <div className="mt-3 space-y-2">
        {data.plans.slice(0, 3).map((p, i) => (
          <div key={p.plan_index} className="rounded-xl border border-line/80 bg-panel/40">
            <button
              type="button"
              onClick={() => setOpen(open === i ? -1 : i)}
              className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
            >
              <span className="text-sm font-semibold text-ink">
                Plan {p.plan_index}
                {i === 0 && <span className="ml-2 text-xs font-medium text-ok">best</span>}
              </span>
              <span className="flex items-center gap-2 text-xs text-ink-2">
                highest worker {pts(p.max_projected_points)}
                <ChevronDown size={14} className={open === i ? 'rotate-180 transition' : 'transition'} />
              </span>
            </button>
            {open === i && (
              <ul className="space-y-1 border-t border-line/70 px-3 py-2">
                {[...p.assignments].sort((a, b) => a.position - b.position).map((a) => (
                  <li key={a.task_id} className="flex items-start justify-between gap-3 text-xs">
                    <span className="text-ink">{a.task_title} <span className="text-ink-3">→ {a.worker_name}</span></span>
                    <span className="shrink-0 font-medium text-[#B97512]">+{pts(a.additional_points)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
      {data.blocked_workers.length > 0 && (
        <p className="mt-3 flex items-start gap-1.5 text-xs text-ink-2">
          <UserX size={14} className="mt-px shrink-0 text-ink-3" />
          {data.blocked_workers.map((b) => b.reason).join(' ')}
        </p>
      )}
      <p className="mt-2 text-[11px] leading-snug text-ink-3">{data.label}. A supervisor must approve any assignment.</p>
    </Card>
  );
}
