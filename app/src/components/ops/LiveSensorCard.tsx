// STUB: replaced by the build session that owns src/components/ops/. Keep this export name and props.
// `band` mirrors the Vexo SDK (see BandState in data/types.ts); `controls` comes from useVexoBand.
import type { VexoBand } from '../../band/useVexoBand';
import type { BandState } from '../../data/types';
import { Placeholder } from '../ui/primitives';

export function LiveSensorCard(_props: { band: BandState; controls?: Omit<VexoBand, 'state'> }) {
  return <Placeholder name="Live sensor (Vexo band)" className="h-full" />;
}
