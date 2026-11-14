// The one place in the dashboard that downloads anything after the page itself. It can
// only fetch the file named below, which sits in this folder on the same server as the
// page. It cannot be given a web address: a name that is not on the list is refused, and
// the browser is told to refuse any answer that comes from, or redirects to, another place.
import { SAMPLE_FILE } from './pure.js';

const ALLOWED = new Set([SAMPLE_FILE]);       // the pretend band's voice
const HERE = import.meta.url.slice(0, import.meta.url.lastIndexOf('/') + 1);    // this folder

/**
 * Download one of the listed files from this folder.
 * @param {string} name SAMPLE_FILE
 * @returns {Promise<Uint8Array>}
 */
export async function loadFile(name) {
  if (!ALLOWED.has(name)) throw new Error(`"${name}" is not one of the files this page may load.`);
  const response = await fetch(HERE + name, { mode: 'same-origin', credentials: 'same-origin', redirect: 'error' });
  if (!response.ok) throw new Error(`Could not load ${name} (the server answered ${response.status}).`);
  return new Uint8Array(await response.arrayBuffer());
}
