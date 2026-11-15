// Small helpers shared by the tests.
import { readFileSync } from 'node:fs';

export const hex = (bytes) => Buffer.from(bytes).toString('hex');
export const unhex = (text) => new Uint8Array(Buffer.from(text, 'hex'));
export const vectors = JSON.parse(readFileSync(new URL('../../contract/test-vectors.json', import.meta.url), 'utf8'));

/** The vectors write the byte fields `opus` and `value` as hex; code uses Uint8Array. */
export function withBytes(fields) {
  const out = { ...fields };
  for (const key of ['opus', 'value']) if (typeof out[key] === 'string') out[key] = unhex(out[key]);
  return out;
}

/** The other way round, so decoded objects can be compared with the vectors. */
export function withHex(fields) {
  const out = { ...fields };
  for (const key of ['opus', 'value']) if (out[key] instanceof Uint8Array) out[key] = hex(out[key]);
  return out;
}

/** A decoded frame in the shape the vectors use. */
export function likeVector(decoded) {
  if (decoded && decoded.frame === 'response') return { ...decoded, data: withHex(decoded.data) };
  return decoded && withHex(decoded);
}
