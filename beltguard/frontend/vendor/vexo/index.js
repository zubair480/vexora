export { Band } from './band.js';
export { Transport, VexoError } from './transport.js';
export { SimTransport } from './transports/sim.js';
export { WebBluetoothTransport } from './transports/web-bluetooth.js';
export { WebSerialTransport } from './transports/web-serial.js';
export { BandAudio, canDecodeAudio, toInt16, oggPackets, SeqTracker, Resampler, AUDIO_RATE, AUDIO_FRAME_MS } from './audio.js';
export { bandName, bandId, NAME_PREFIX, SERVICE_UUID, CONTRACT_REVISION, EVENT_KINDS, LED_PATTERNS } from './codec.js';
// The whole byte layer, for people who want to look inside: codec.encodeCommand(...), ...
export * as codec from './codec.js';
