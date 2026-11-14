// The motions of the pretend hand, for the buttons in the dashed box. Each one says what
// the sensor reads `s` seconds into the motion: ax ay az in g, gx gy gz in degrees/second.
// They are played with the kit's pretend band: pretend.playMotion(shape, { seconds }).
// A real band needs none of this. The test uses the same motions.
const turn = 2 * Math.PI;

export const MOTIONS = {
  // One round circle in a second: two axes take turns.
  circle: [(s) => ({ gx: 220 * Math.sin(turn * s), gy: 220 * Math.cos(turn * s), ax: 0.4 * Math.cos(turn * s), ay: 0.4 * Math.sin(turn * s) }), 1.0],
  // Fast left-right-left-right.
  shake: [(s) => ({ ax: 1.2 * Math.sin(turn * 4 * s), gz: 120 * Math.cos(turn * 4 * s) }), 0.75],
  // One quick turn of the wrist and back to rest.
  flick: [(s) => ({ gz: 420 * Math.sin(turn * s / 0.8), ay: 0.5 * Math.sin(turn * s / 0.8) }), 0.4],
  // A straight push forward and back.
  punch: [(s) => ({ ax: 1.5 * Math.sin(turn * s / 0.5), az: 1 - 0.6 * Math.sin(turn * s / 1.0), gy: -90 * Math.sin(turn * s / 1.0) }), 0.5],
};
