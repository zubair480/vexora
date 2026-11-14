// Private helpers for the ops cards: a calm empty state and the "—" shown for a null value.
import type { ReactNode } from 'react';
import { Card, CardHeader } from '../ui/primitives';

/** Shown in place of a value the server has no real source for. Never replace with a made-up number. */
export const DASH = '—';

export function OpsEmptyCard({
  icon,
  title,
  message,
  minH = 'min-h-[248px]',
}: {
  icon: ReactNode;
  title: ReactNode;
  message: string;
  minH?: string;
}) {
  return (
    <Card className={`flex h-full ${minH} flex-col p-5`}>
      <CardHeader icon={icon} title={title} />
      <div className="mt-4 flex flex-1 items-center justify-center rounded-xl bg-panel/60 p-6 text-center">
        <span className="label">{message}</span>
      </div>
    </Card>
  );
}
