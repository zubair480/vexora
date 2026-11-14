import { useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Box,
  CalendarClock,
  CheckCircle2,
  ClipboardList,
  Flag,
  Pencil,
  Send,
  Users,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import { Card, CardHeader, Pill, type Tone } from '../ui/primitives';
import type { WorkOrder } from '../../data/types';

type WoStatus = WorkOrder['status'];
type Priority = WorkOrder['priority'];

const fade = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -6 },
  transition: { duration: 0.22 },
};

const STATUS_PILL: Record<WoStatus, { tone: Tone; dot: boolean; label: string }> = {
  draft: { tone: 'warn', dot: false, label: 'Draft' },
  submitted: { tone: 'info', dot: true, label: 'Submitted' },
  approved: { tone: 'ok', dot: false, label: 'Approved' },
};

const PRIORITY: Record<Priority, { tone: Tone; label: string }> = {
  high: { tone: 'bad', label: 'High' },
  medium: { tone: 'warn', label: 'Medium' },
  low: { tone: 'neutral', label: 'Low' },
};

const focusRing = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/40';

function Row({ icon: Icon, label, children }: { icon: LucideIcon; label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[20px_88px_1fr] items-center gap-2 py-2 text-sm">
      <Icon size={15} strokeWidth={1.75} className="text-ink-3" aria-hidden />
      <span className="text-ink-2">{label}</span>
      <span className="min-w-0 font-medium leading-snug text-ink">{children}</span>
    </div>
  );
}

export function WorkOrderCard({ workOrder }: { workOrder: WorkOrder }) {
  const [status, setStatus] = useState<WoStatus>(workOrder.status);
  const pill = STATUS_PILL[status];
  const priority = PRIORITY[workOrder.priority];
  const isDraft = status === 'draft';

  // The Submit/Undo swap unmounts the focused button; move focus to its counterpart once it has entered.
  const submitRef = useRef<HTMLButtonElement>(null);
  const undoRef = useRef<HTMLButtonElement>(null);
  const pendingFocus = useRef<'submit' | 'undo' | null>(null);
  const restoreFocus = () => {
    const target = pendingFocus.current === 'undo' ? undoRef.current : pendingFocus.current === 'submit' ? submitRef.current : null;
    if (target) {
      target.focus();
      pendingFocus.current = null;
    }
  };
  const submit = () => {
    pendingFocus.current = 'undo';
    setStatus('submitted');
  };
  const undo = () => {
    pendingFocus.current = 'submit';
    setStatus('draft');
  };

  return (
    <Card className="flex h-full min-h-[248px] flex-col p-5">
      <CardHeader
        icon={<ClipboardList size={18} strokeWidth={1.75} />}
        title={isDraft ? 'Draft Work Order' : 'Work Order'}
        subtitle={workOrder.id}
        right={
          <AnimatePresence mode="wait" initial={false}>
            <motion.span key={status} layout className="shrink-0" {...fade}>
              <Pill tone={pill.tone} dot={pill.dot}>
                {pill.label}
              </Pill>
            </motion.span>
          </AnimatePresence>
        }
      />

      <div className="mt-4 divide-y divide-line">
        <Row icon={Box} label="Asset">
          {workOrder.asset}
        </Row>
        <Row icon={Wrench} label="Task">
          {workOrder.task}
        </Row>
        <Row icon={Flag} label="Priority">
          <Pill tone={priority.tone}>{priority.label}</Pill>
        </Row>
        <Row icon={CalendarClock} label="Planned">
          {workOrder.planned}
        </Row>
        <Row icon={Users} label="Assigned">
          {workOrder.assignee}
        </Row>
      </div>

      <div className="mt-auto pt-4" aria-live="polite">
        <AnimatePresence mode="wait" initial={false}>
          {isDraft ? (
            <motion.div key="draft" className="flex flex-wrap justify-end gap-2" {...fade} onAnimationComplete={restoreFocus}>
              <button
                type="button"
                aria-disabled="true"
                title="Editing coming soon"
                className={`inline-flex cursor-not-allowed items-center gap-1.5 rounded-xl border border-line bg-surface px-3.5 py-2 text-sm font-medium text-ink opacity-60 ${focusRing}`}
              >
                <Pencil size={14} strokeWidth={1.75} />
                Edit
              </button>
              <button
                ref={submitRef}
                type="button"
                onClick={submit}
                className={`inline-flex items-center gap-1.5 rounded-xl bg-ok px-3.5 py-2 text-sm font-semibold text-surface shadow-card transition-colors hover:bg-ok/90 ${focusRing}`}
              >
                <Send size={14} strokeWidth={1.75} />
                Submit for Approval
              </button>
            </motion.div>
          ) : (
            <motion.div
              key="submitted"
              className="flex items-center gap-2 rounded-xl bg-ok-bg px-3 py-2 text-sm text-ok"
              {...fade}
              onAnimationComplete={restoreFocus}
            >
              <CheckCircle2 size={16} strokeWidth={1.75} className="shrink-0" />
              <span className="min-w-0 flex-1 font-medium">
                {status === 'approved' ? 'Approved' : 'Submitted for approval'}
                {status === 'submitted' && <span className="font-normal"> · just now</span>}
              </span>
              {status === 'submitted' && (
                <button
                  ref={undoRef}
                  type="button"
                  onClick={undo}
                  className={`shrink-0 rounded text-xs text-ink-2 underline underline-offset-2 transition-colors hover:text-ink ${focusRing}`}
                >
                  Undo
                </button>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </Card>
  );
}
