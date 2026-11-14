// Put the tool that is (or was) in use onto its station, so the route map shows tool activity
// at e.g. Station 3. The server links a tool session to the station and work order that were open
// while the tool ran ("derived: open work order"); this only adds that to the station's note.
import type { Conveyor, Tool } from './types';

export function withToolActivity(conveyor: Conveyor, tool: Tool | null): Conveyor {
  if (!tool?.stationId) return conveyor;
  return {
    ...conveyor,
    stations: conveyor.stations.map((s) => {
      if (s.id !== tool.stationId) return s;
      const parts = [
        `${tool.name} ${tool.inUse ? 'in use now' : 'used here'}`,
        `${tool.minutesToday} min today`,
        tool.workOrderId ? `for ${tool.workOrderId}` : null,
      ].filter(Boolean);
      const toolNote = parts.join(' · ');
      return {
        ...s,
        status: s.status === 'ok' && tool.inUse ? 'attention' : s.status,
        note: s.note ? `${s.note} · ${toolNote}` : toolNote,
      };
    }),
  };
}
