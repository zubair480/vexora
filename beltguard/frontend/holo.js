// holo.js — BeltGuard 3D conveyor scene. Owned by S0 (orchestrator). S1's app.js calls it:
//   import { createHolo } from './holo.js';
//   const holo = createHolo(document.getElementById('stage'), { onStationClick: id => ... });
//   holo.update(state)           // snapshot `state` from /ws (see CONTRACT.md)
//   holo.activity(state, conf)   // detector state: idle|walking|gesture|candidate|tool_active|unresolved
//   holo.motion(rmsG)            // live wrist vibration intensity (accel RMS in g, gravity removed)
//   holo.destroy()
// Temporary stub while S0 writes the full scene (lands in a few minutes).
export function createHolo(container, opts = {}) {
  return { update() {}, activity() {}, motion() {}, destroy() {} };
}
export default createHolo;
